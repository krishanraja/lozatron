"""Deterministic near-duplicate clustering.

One event currently arrives as three stories from three outlets, and all three
ship. Fingerprinting is URL-based, so the same wire story on Variety and
Deadline yields two different keys and neither suppresses the other.

Clustering groups them into one entry carrying every outlet. That does two
things at once: it stops the brief reading like a feed, and the number of
independent outlets carrying a story becomes a corroboration signal that is
actually earned, unlike a model's self-rated confidence.

Pure functions, no I/O, integer arithmetic throughout so results are
reproducible and diffable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import unicodedata

from typing import Callable

from .core import (
    ANALYSIS_WINDOW_HOURS, Story, eligible, eligible_analysis, freshness_tier,
    normalize_url, rank, relevance,
)

# Words carrying no discriminating power in a headline.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from
with without by as is are was were be been being has have had do does did will
would can could may might must shall should its it his her their our your my
after before during over under about into out up down off again more most some
such no nor not only own same so too very just now here there when where why how
all any both each few other own s t don now amid via
""".split())

# Domain noise: present in nearly every candidate, so it inflates similarity
# toward false merges rather than distinguishing anything.
DOMAIN_NOISE = frozenset("""
creator creators economy influencer influencers content new news report reports
says said exclusive update updates announce announces announced launch launches
first latest top best big biggest major
""".split())

# The vocabulary of a news headline that is NOT an entity: the verbs outlets
# use for corporate events, and the generic nouns that frame them.
#
# This is what lets capitalisation work as a proper-noun signal again. The old
# code gave up on it in Title Case headlines and leaned on a 76-name vocabulary
# instead, which meant any company not on that list produced ZERO anchors. On
# 6 October Lauren received the same acquisition twice, as two numbered
# stories, because "THE-TEAM Acquires UK Creator Agency Outreach Talent Group"
# and "The Team Buys U.K. Creators Agency In Expansion Move" between them
# yielded no anchors at all and overlapped on barely a third of their words.
#
# Inverting it is far more robust: a capitalised token that is not one of these
# common headline words is almost always a name, and there are only so many
# ways a trade publication can say "bought".
HEADLINE_COMMON = frozenset("""
acquires acquire acquired acquisition buys buy bought purchase purchases
takeover merges merger acquiring snaps launches launch launched launching
unveils unveil debuts opens open opening starts start rolls raises raise
raised funding closes close signs sign signed signing inks ink hires hire
hired hiring names name named appoints appoint appointed promotes promoted
taps tap joins join joining leaves exits exit departs steps backs back
invests invest investment funds fund partners partner partnership deal deals
sells sell sold sale divests spins spinoff expands expand expansion grows
grow growth adds add adding brings bring takes take makes make gets get
plans plan eyes eye weighs weigh mulls explores exploring considers sets set
builds build building moves move launches wins win lands land secures secure
boosts boost cuts cut drops drop ends end halts halt pauses returns return
agency agencies group company companies firm business businesses division
unit units arm team teams platform platforms service services product
products brand brands market markets industry sector move moves push pushes
bid bids stake shares share holding venture ventures studio studios
million billion thousand percent year years month months week weeks day days
move expansion reach deal stake roster bench
""".split())

# Retained for the handful of lowercase platform names that would otherwise be
# missed, since those arrive uncapitalised in handles and URLs.
ANCHOR_VOCAB = frozenset("""
youtube tiktok instagram facebook meta snapchat snap twitch kick spotify apple
amazon netflix disney google alphabet twitter threads bluesky patreon substack
kajabi teachable shopify roblox discord reddit linkedin pinterest microsoft
paramount warner comcast nbcuniversal fox sony universal hulu peacock max
mrbeast ksi sidemen logan jake paul chamberlain cooper rogan hormozi
colin samir markiplier pewdiepie dude perfect hasan pokimane ninja kaicenat
tubefilter deadline variety digiday techcrunch verge podnews passionfruit
linktree beehiiv ghost gumroad cameo whatnot fanfix onlyfans
""".split())

# "U.K." tokenises to ["U", "K"] under a word regex, so an outlet writing
# U.K. and one writing UK share nothing. Collapse the dots first.
DOTTED = re.compile(r"\b(?:[A-Za-z]\.){2,}")

MONEY = re.compile(r"^\$?\d[\d,.]*[mbk]?$", re.IGNORECASE)
# "$400 Million" and "$400M" are the same amount written two ways. Left
# unnormalised they tokenise as {400, million} and {400m}, share no anchor, and
# two outlets covering one ruling ship as two stories.
MONEY_SCALE = {"million": "m", "billion": "b", "thousand": "k", "m": "m", "bn": "b", "b": "b", "k": "k"}
MONEY_PHRASE = re.compile(
    r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(million|billion|thousand|bn|[mbk])\b", re.IGNORECASE
)


def normalise_money(title: str) -> str:
    """Rewrite every amount into one canonical token, e.g. `400m`."""
    def swap(match: re.Match[str]) -> str:
        number = match.group(1).replace(",", "").rstrip(".")
        if number.endswith(".0"):
            number = number[:-2]
        return f" {number}{MONEY_SCALE[match.group(2).lower()]} "
    return MONEY_PHRASE.sub(swap, title)
# " - Variety", " | Deadline", " — The Hollywood Reporter"
PUBLISHER_TAIL = re.compile(r"\s*[|–—-]\s*[A-Z][\w .&'’]{2,30}$")
WORD = re.compile(r"[^\W_]+", re.UNICODE)

MERGE_ANCHORED = 72     # similar enough, and the proper nouns agree
MERGE_IDENTICAL = 88    # near-identical wire copy, no anchors needed
MERGE_BY_ANCHOR = 28    # different words, same company and same amount
MERGE_BY_ACTION = 40    # same company, same kind of corporate action
MERGE_BY_TOPIC = 40     # same company plus shared subject matter
MERGE_TOPIC_TOKENS = 2  # shared words BESIDES the shared name

# Outlets describe one corporate event with different verbs: one writes
# "Acquires", another "Buys", a third "Snaps Up". Without this, a cross-outlet
# duplicate survives whenever no price is quoted, because the money-anchor path
# below needs a figure to fire. That is how Lauren received the THE-TEAM
# acquisition twice on 6 October.
ACTION_CLASSES = {
    "acquisition": """acquires acquire acquired acquiring acquisition buys buy
        bought purchase purchased takeover snaps absorbs absorbed""".split(),
    "launch": """launches launch launched launching unveils unveiled debuts
        debuted introduces introduced opens opened rolls""".split(),
    "funding": """raises raise raised funding funds raising invests invested
        investment backs backed valuation round""".split(),
    "hire": """hires hired hiring names named appoints appointed promotes
        promoted taps tapped elevates elevated""".split(),
    "exit": """exits exit departs departed leaves left steps resigns resigned
        ousted out""".split(),
    "partnership": """partners partnered partnership teams joins joined
        collaborates collaboration signs signed inks inked""".split(),
    "sale": """sells sell sold sale divests divested spins spun offloads""".split(),
    "shutdown": """shuts shut closes closed ends ended halts halted kills
        killed cancels cancelled discontinues""".split(),
}
_ACTION_BY_WORD = {
    word: name for name, words in ACTION_CLASSES.items() for word in words
}


def action_class(title: str) -> str | None:
    """Which kind of corporate event this headline describes, if any.

    First match wins on word order, because a headline leads with its verb.
    """
    for word in WORD.findall(title.casefold()):
        found = _ACTION_BY_WORD.get(word) or _ACTION_BY_WORD.get(_stem(word))
        if found:
            return found
    return None

# "Fewer strong stories is better than a full list with weak/stale items.
# Thin-flow honesty beats filler." Widening the relevance gate to hit the 7-10
# target pulled in a tail of weak matches -- a laptop launch, a naval
# procurement list -- so a floor keeps the tail out. A quiet day now produces a
# short brief rather than a padded one, and an empty briefing sends nothing.
# The floor is now a RELEVANCE floor, not a rank floor. When it applied to
# rank, recency was inside the number and a 25-hour-old story needed three
# strong business terms to clear a bar a three-hour-old story cleared with
# none -- which silently cut the declared 48-hour window down to eight and
# starved the brief. 3 means, in practice, one strong business term plus a
# creator term. Crucially it requires at least one STRONG term, so a story has
# to report a business event rather than merely be about creators: at 3, "A
# creator reflects on the year" cleared the bar on the creator signal alone.
MIN_RELEVANCE = 4

# Kept for the legacy `select_stories` diagnostic path only.
MIN_SCORE = 8


def _stem(token: str) -> str:
    """Strip a light inflection when at least four characters survive."""
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def canon_tokens(title: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (all_tokens, anchor_tokens) for a headline.

    Anchors are the load-bearing half. Without them "YouTube announces creator
    monetization change" and "TikTok announces creator monetization change" can
    merge into one entry badged as two independent sources -- a correctness
    failure that reads like a feature. Differing anchors block that.
    """
    stripped = normalise_money(PUBLISHER_TAIL.sub("", title.strip()))
    stripped = DOTTED.sub(lambda m: m.group(0).replace(".", ""), stripped)
    normalized = unicodedata.normalize("NFKD", stripped)
    raw = WORD.findall(normalized)

    tokens: set[str] = set()
    anchors: set[str] = set()
    for index, word in enumerate(raw):
        lowered = word.casefold()
        if MONEY.match(word):
            anchors.add(lowered)
            tokens.add(lowered)
            continue
        if lowered in STOPWORDS or lowered in DOMAIN_NOISE or len(lowered) < 2:
            continue
        stemmed = _stem(lowered)
        tokens.add(stemmed)
        if lowered in ANCHOR_VOCAB or stemmed in ANCHOR_VOCAB:
            anchors.add(stemmed)
        # A capitalised token that is not a common headline word is a name.
        # This now applies in Title Case headlines too, which is where the
        # old title-case veto left trade copy with no anchors whatsoever.
        elif (word[:1].isupper() and lowered not in HEADLINE_COMMON
                and stemmed not in HEADLINE_COMMON
                and (len(word) >= 3 or word.isupper())):
            anchors.add(stemmed)
    return frozenset(tokens), frozenset(anchors)


def _grams(tokens: frozenset[str]) -> frozenset[str]:
    """Character 4-grams over the sorted token string.

    Rescues morphological variants the deliberately-simple stemmer misses.
    """
    joined = " ".join(sorted(tokens))
    return frozenset(joined[i:i + 4] for i in range(max(0, len(joined) - 3)))


def similarity(a: frozenset[str], b: frozenset[str]) -> int:
    """Headline similarity in hundredths. Integer arithmetic only."""
    if not a or not b:
        return 0
    overlap = len(a & b)
    jaccard = 100 * overlap // len(a | b)
    # Containment is discounted so a short headline is not simply swallowed by
    # a long one that happens to contain all of its words.
    containment = (100 * overlap // min(len(a), len(b))) * 9 // 10
    ga, gb = _grams(a), _grams(b)
    grams = 100 * len(ga & gb) // max(1, len(ga | gb))
    return max(jaccard, containment, grams)


def _hard(anchors: frozenset[str]) -> frozenset[str]:
    """Anchors that are amounts or numbers: the least coincidental kind."""
    return frozenset(a for a in anchors if MONEY.match(a) or any(ch.isdigit() for ch in a))


def should_merge(score: int, anchors_a: frozenset[str], anchors_b: frozenset[str],
                 action_a: str | None = None, action_b: str | None = None,
                 tokens_a: frozenset[str] = frozenset(),
                 tokens_b: frozenset[str] = frozenset()) -> bool:
    if score >= MERGE_IDENTICAL:
        return True
    shared_names = anchors_a & anchors_b
    # Same named entity, same kind of corporate action, and enough word overlap
    # to be about one event rather than two on the same day. All three are
    # required: "Spotify Acquires Wondery" and "Spotify Buys Megaphone" share a
    # name and an action class and must NOT merge, which the score bar blocks.
    if (shared_names and action_a is not None and action_a == action_b
            and score >= MERGE_BY_ACTION):
        return True
    # A commentary headline carries no verb to classify. "TikTok Expands
    # Off-Platform Ad Network to U.S. Advertisers, Adds AI Campaign Tools" and
    # "TikTok's ad network bet: More reach, more budget" are one announcement,
    # and neither the action path nor the strict threshold reaches them.
    #
    # The subject matter has to be shared, not just the company. Two stories
    # about the same company on the same day overlap on the company ALONE:
    # "Spotify Acquires Wondery" and "Spotify Buys Megaphone" share `spotify`
    # and nothing else, so counting shared words BESIDES the name is what
    # separates one event from two.
    shared_subject = (tokens_a & tokens_b) - shared_names
    if (shared_names and score >= MERGE_BY_TOPIC
            and len(shared_subject) >= MERGE_TOPIC_TOKENS):
        return True
    # Two outlets can describe one event in almost no shared words. "Judge Casts
    # Doubt on TikTok's $400 Million Privacy Deal" and "U.S. Judge Signals
    # Rejection of Key Piece in TikTok's $400M Privacy Settlement" overlap on
    # barely a fifth of their tokens. What they do share is the company and the
    # amount, and a named company plus a specific figure is not a coincidence.
    shared = shared_names
    if len(shared) >= 2 and _hard(shared) and score >= MERGE_BY_ANCHOR:
        return True
    if score < MERGE_ANCHORED:
        return False
    # Both sides have anchors and they disagree -> different events.
    if anchors_a and anchors_b:
        return bool(anchors_a & anchors_b)
    # One side has no proper nouns at all; fall back to the strict threshold.
    return False


@dataclasses.dataclass(slots=True)
class Cluster:
    """One event, and every outlet that carried it."""

    leader: Story
    members: list[Story] = dataclasses.field(default_factory=list)
    tokens: frozenset[str] = frozenset()
    anchors: frozenset[str] = frozenset()
    # Which kind of corporate event the leader describes, so an outlet writing
    # "Buys" can be matched to one writing "Acquires".
    action: str | None = None
    score: int = 0
    # Integer hours, set at selection. The analyst is given this instead of a
    # timestamp so it cannot reason about -- or invent -- dates.
    age_hours: int = 0
    relevance: int = 0
    freshness: str = ""

    @property
    def key(self) -> str:
        """Presentation and archive identifier -- never the dedup primitive.

        Derived from leader tokens, so it is not stable across runs when a
        different member happens to be earliest. Deduplication stays on
        per-story fingerprints for exactly that reason.
        """
        basis = " ".join(sorted(self.tokens)) or self.leader.title.casefold()
        return "cl_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    # Tiers that are not an outlet doing the work: chatter, one person's post,
    # and a site republishing somebody else's article.
    NOT_AN_OUTLET = frozenset({"community", "social", "syndicated"})

    @property
    def outlets(self) -> list[str]:
        """The outlets that actually reported this, named once each.

        A republisher is excluded. The brief told Lauren a story was carried
        by "Digiday and Biztoc.com", which reads as two outlets agreeing when
        it is one article and a scrape of it.
        """
        seen: dict[str, None] = {}
        for story in self.members:
            if story.tier in self.NOT_AN_OUTLET:
                continue
            seen.setdefault(story.source, None)
        if not seen:
            # Nothing but chatter or copies. Name what there is rather than
            # render an entry with no attribution at all; `confirmed` stops
            # this shipping as a story anyway.
            for story in self.members:
                seen.setdefault(story.source, None)
        return list(seen)

    @property
    def corroboration(self) -> int:
        """Independent outlets carrying this story. The honest confidence signal."""
        return len(self.outlets)

    @property
    def tiers(self) -> set[str]:
        return {story.tier for story in self.members}

    @property
    def confirmed(self) -> bool:
        """Reddit is signal, never proof.

        Lauren's rule: use Reddit to discover chatter, then confirm via a trade,
        primary or direct source before including. So a cluster whose every
        member is community chatter is not deliverable. Left unenforced, the
        subreddits flood the pool with tech-support questions, beginner advice
        and streamer drama, which is exactly what it produced when measured.

        Social is held to the same bar for the same reason, and so is a
        syndicated copy: a scrape of an article is not a second outlet
        confirming it. A cluster needs at least one member that is reporting --
        trade, primary or analysis -- before it can ship as a story.
        """
        return bool(self.tiers - self.NOT_AN_OUTLET)

    @property
    def primary(self) -> bool:
        return "primary" in self.tiers

    def member_keys(self) -> list[str]:
        return [story.key for story in self.members]


def build_clusters(stories: list[Story]) -> list[Cluster]:
    """Group stories into events.

    Candidates are compared against each cluster's *leader* only, never against
    any member. That is what keeps assignment deterministic and prevents
    transitive chain-merge, where A~B and B~C but A is unlike C, collapsing
    three separate events into one blob.
    """
    # Republished copies and chatter sort last, so a cluster is led by an
    # outlet that did the work. Within each group the order is still
    # deterministic, which is what keeps assignment reproducible.
    ordered = sorted(stories, key=lambda s: (
        s.tier in Cluster.NOT_AN_OUTLET, s.published_at, normalize_url(s.url), s.title))
    clusters: list[Cluster] = []
    for story in ordered:
        tokens, anchors = canon_tokens(story.title)
        action = action_class(story.title)
        for cluster in clusters:
            if should_merge(similarity(tokens, cluster.tokens), anchors, cluster.anchors,
                            action, cluster.action, tokens, cluster.tokens):
                cluster.members.append(story)
                break
        else:
            clusters.append(Cluster(leader=story, members=[story], tokens=tokens,
                                    anchors=anchors, action=action))
    return clusters


def select_clusters(
    stories: list[Story],
    delivered: Callable[[Story], bool],
    *,
    now,
    window_hours: int,
    limit: int,
) -> list[Cluster]:
    """Gate, cluster, suppress and rank -- the clustered replacement for
    `core.select_stories`.

    Suppression is per-cluster: if Lauren has already been sent *any* member,
    the event is not new, whichever outlet's copy arrived first. Ordering stays
    with the deterministic `rank`, applied to the cluster leader.
    """
    # Social never enters the story path at all. Not gated, not ranked, not
    # capped -- excluded, because the reliable way to guarantee a post never
    # appears as a numbered entry is for it never to be a candidate. It is
    # routed to `commentary()` instead.
    newsworthy = [story for story in stories if story.tier != "social"]
    fresh = [story for story in newsworthy if eligible(story, now, window_hours)]
    clusters = [
        cluster for cluster in build_clusters(fresh)
        if cluster.confirmed and not any(delivered(member) for member in cluster.members)
    ]
    for cluster in clusters:
        cluster.score = rank(cluster.leader, now)
        cluster.relevance = relevance(cluster.leader)
        # Corroboration is earned evidence: two independent outlets carrying
        # the same event is the strongest non-model signal available.
        cluster.relevance += min(2, cluster.corroboration - 1)
        # The +3 primary bonus that used to live here has been removed.
        #
        # It was built from the recorded triage rule "a creator's own tweet
        # about a deal outranks a Deadline story about the same deal". In
        # practice that ranks one person's post above reporting, which is the
        # opposite of what the brief is for: a post is commentary and gets
        # handled as commentary. Nothing in the pool has ever carried the
        # "primary" tier, so removing it changes no output today -- it removes
        # a trap that would have fired the moment paid sources came on.
        cluster.freshness = freshness_tier(cluster.leader, now)
        cluster.age_hours = max(0, round((now - cluster.leader.published_at).total_seconds() / 3600))
    floor = int(os.environ.get("LOZ_MIN_RELEVANCE") or MIN_RELEVANCE)
    clusters = [cluster for cluster in clusters if cluster.relevance >= floor]
    clusters.sort(key=lambda c: (-c.score, -c.corroboration, -c.leader.published_at.timestamp()))
    return cap_per_source(clusters, limit)


def cap_per_source(clusters: list[Cluster], limit: int, per_source: int = 2) -> list[Cluster]:
    """Stop one outlet taking the whole brief.

    The live email Krish flagged was three NetInfluencer stories in a row, and
    the historical telemetry shows the same skew: NetInfluencer delivered 16 of
    61, more than triple any other source. Applied after ranking, so it removes
    the third story from an outlet rather than reordering anything.
    """
    seen: dict[str, int] = {}
    kept: list[Cluster] = []
    for cluster in clusters:
        source = cluster.leader.source
        if seen.get(source, 0) >= per_source:
            continue
        seen[source] = seen.get(source, 0) + 1
        kept.append(cluster)
        if len(kept) >= limit:
            break
    return kept


# --- commentary: what individuals are saying, never what happened -----------

# A pattern needs this many DISTINCT accounts before it is worth a line. Two
# people posting the same thought is a coincidence; it is also exactly how a
# single loud thread gets mistaken for a trend.
PATTERN_MIN_ACCOUNTS = 3

# How closely a post has to track a story before it counts as chatter about
# that story. Lower than the story-merge bar, because a post paraphrases
# rather than reproduces a headline, and it is only ever attaching a count --
# a wrong attachment costs a number, not a false story.
COMMENTARY_MATCH = 40


@dataclasses.dataclass(slots=True)
class Pattern:
    """A theme several independent accounts converged on, with no story behind it."""
    tokens: frozenset[str]
    accounts: list[str] = dataclasses.field(default_factory=list)
    posts: list[Story] = dataclasses.field(default_factory=list)

    @property
    def label(self) -> str:
        """The shared words, longest first, as a plain phrase."""
        return " ".join(sorted(self.tokens, key=len, reverse=True)[:4])


def _account(story: Story) -> str:
    return story.title.strip().casefold() or story.url


def attach_commentary(clusters: list[Cluster], social: list[Story]) -> dict[str, list[Story]]:
    """Map cluster key -> posts discussing it.

    Commentary attaches to reporting; it never becomes reporting. A cluster
    that picks up chatter is a cluster we can say is being talked about, which
    is a genuinely useful signal for a President deciding what to cover. It
    does not change the cluster's rank, because how loud a story is on social
    is not the same as how much it matters.
    """
    if not clusters or not social:
        return {}
    found: dict[str, list[Story]] = {}
    for post in social:
        post_tokens, _ = canon_tokens(post.summary[:280])
        if not post_tokens:
            continue
        best, best_score = None, 0
        for cluster in clusters:
            score = similarity(cluster.tokens, post_tokens)
            if score > best_score:
                best, best_score = cluster, score
        if best is not None and best_score >= COMMENTARY_MATCH:
            found.setdefault(best.key, []).append(post)
    return found


def find_patterns(social: list[Story], attached: dict[str, list[Story]],
                  *, min_accounts: int = PATTERN_MIN_ACCOUNTS) -> list[Pattern]:
    """Themes that several independent accounts raised, with no story behind them.

    This is the only route by which social reaches the brief on its own, and
    it reaches it as an aggregate -- "several creators are discussing X" --
    never as a quoted post. One person's opinion is not news and is not
    interesting; a dozen people independently raising the same thing is a
    signal about where the industry's attention is.

    Posts already attached to a story are excluded, because their theme is
    that story and reporting it twice would be padding.
    """
    used = {post.url for posts in attached.values() for post in posts}
    loose = [post for post in social if post.url not in used]
    patterns: list[Pattern] = []
    for post in sorted(loose, key=lambda item: item.url):
        tokens, _ = canon_tokens(post.summary[:280])
        if len(tokens) < 3:
            continue
        for pattern in patterns:
            if similarity(pattern.tokens, tokens) >= COMMENTARY_MATCH:
                pattern.tokens &= tokens
                pattern.posts.append(post)
                if _account(post) not in pattern.accounts:
                    pattern.accounts.append(_account(post))
                break
        else:
            patterns.append(Pattern(tokens=tokens, accounts=[_account(post)], posts=[post]))
    # Distinct accounts, not distinct posts: one person posting six times is
    # one person, and counting posts is how a single thread becomes a "trend".
    return [item for item in patterns if len(item.accounts) >= min_accounts]


# --- the mechanics track ----------------------------------------------------

# Two. It is a companion to the news, not a second brief, and rule 2 -- fewer
# strong items beats a full list -- applies here more than anywhere, because
# these pieces sit in a seven-day window and would otherwise accumulate.
MAX_ANALYSIS = 2


def select_analysis(stories: list[Story], delivered: Callable[[Story], bool], *,
                    now, window_hours: int = ANALYSIS_WINDOW_HOURS,
                    limit: int = MAX_ANALYSIS) -> list[Cluster]:
    """Pick the mechanics items. Separate path, separate window, separate cap.

    Clustered like the news so two outlets covering the same move collapse,
    but never merged into the news selection: the seven-day window means these
    would outnumber the news on any quiet day, and rule 2 says the answer to a
    quiet day is a short brief, not a padded one.

    Ordered newest first rather than by relevance. Every item here has already
    cleared a headline-level mechanics gate, so they are of a kind, and the
    only useful distinction left between them is which one she has not read.
    """
    fresh = [item for item in stories if eligible_analysis(item, now, window_hours)]
    clusters = [
        cluster for cluster in build_clusters(fresh)
        if not any(delivered(member) for member in cluster.members)
    ]
    for cluster in clusters:
        cluster.age_hours = max(0, round((now - cluster.leader.published_at).total_seconds() / 3600))
        cluster.freshness = "mechanics"
    clusters.sort(key=lambda c: -c.leader.published_at.timestamp())
    return cap_per_source(clusters, limit, per_source=1)
