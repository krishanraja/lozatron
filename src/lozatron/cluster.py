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

from .core import Story, eligible, freshness_tier, normalize_url, rank, relevance

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

# Known entities. Capitalisation is unreliable as a proper-noun signal because
# many headlines are Title Case, so a curated vocabulary carries the weight.
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
    normalized = unicodedata.normalize("NFKD", stripped)
    raw = WORD.findall(normalized)
    title_case = sum(1 for word in raw if word[:1].isupper()) > max(1, len(raw) * 6 // 10)

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
        elif not title_case and index > 0 and word[:1].isupper() and len(word) >= 4:
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


def should_merge(score: int, anchors_a: frozenset[str], anchors_b: frozenset[str]) -> bool:
    if score >= MERGE_IDENTICAL:
        return True
    # Two outlets can describe one event in almost no shared words. "Judge Casts
    # Doubt on TikTok's $400 Million Privacy Deal" and "U.S. Judge Signals
    # Rejection of Key Piece in TikTok's $400M Privacy Settlement" overlap on
    # barely a fifth of their tokens. What they do share is the company and the
    # amount, and a named company plus a specific figure is not a coincidence.
    shared = anchors_a & anchors_b
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

    @property
    def outlets(self) -> list[str]:
        seen: dict[str, None] = {}
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

        Social is held to the same bar for the same reason. A cluster needs at
        least one member that is reporting -- trade, primary or analysis --
        before it can ship as a story.
        """
        return bool(self.tiers - {"community", "social"})

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
    ordered = sorted(stories, key=lambda s: (s.published_at, normalize_url(s.url), s.title))
    clusters: list[Cluster] = []
    for story in ordered:
        tokens, anchors = canon_tokens(story.title)
        for cluster in clusters:
            if should_merge(similarity(tokens, cluster.tokens), anchors, cluster.anchors):
                cluster.members.append(story)
                break
        else:
            clusters.append(Cluster(leader=story, members=[story], tokens=tokens, anchors=anchors))
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
