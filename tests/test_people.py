"""The people feed's ranking, offline. `build()` takes plain data, no node."""
import collections

from phunk.models import Cast, Reaction
from phunk.people import CIRCLE_MIN, Profile, build, is_channel, talked_to
from phunk.recommend import Suggestion

NOW = 200_000_000
DAY = 86400
ME = 9
NAMES = {1: "alice", 2: "bob", 3: "carol", 4: "dan"}


def cast(fid, parent_fid=None, mentions=(), ts=NOW):
    return Cast(hash="0x", fid=fid, timestamp=ts, text="", signer="0xk", parent_fid=parent_fid,
                parent_hash="0x1" if parent_fid else None, mentions=list(mentions))


def reaction(target):
    return Reaction(hash="0x", fid=ME, kind="like", timestamp=NOW, signer="0xk", target_fid=target)


def prof(fid, now="warpcast", before=None, last=NOW, channels=()):
    return Profile(fid, app_now=now, app_before=before or now, last_ts=last, channels=list(channels))


def go(**kw):
    args = dict(following=set(), talked=collections.Counter(), suggestions=[], profiles={}, names=NAMES,
                my_app="warpcast", my_channels=set(), now=NOW)
    args.update(kw)
    return build(ME, **args)


def test_talked_to_counts_replies_mentions_and_reactions_but_not_me():
    t = talked_to(ME, [cast(ME, parent_fid=1), cast(ME, mentions=[2, 2, ME]), cast(ME, parent_fid=ME)],
                  [reaction(1), reaction(ME)])
    assert t == {1: 2, 2: 1}


def test_reconnect_only_when_there_is_something_to_do():
    talked = collections.Counter({1: 5, 2: 4, 3: 3, 4: 1})
    profiles = {
        1: prof(1),  # followed, same app, active: nothing to do
        2: prof(2, now="fireflyapp", before="warpcast"),  # moved
        3: prof(3, last=NOW - 20 * DAY),  # quiet
        4: prof(4, now="fireflyapp", before="warpcast"),  # moved, but talked only once
    }
    cards = go(following={1, 2, 3}, talked=talked, profiles=profiles)
    by = {c.fid: c for c in cards}
    assert set(by) == {2, 3}
    assert all(c.kind == "reconnect" for c in cards)
    assert "Now posts from fireflyapp (was warpcast)." in by[2].why
    assert "Quiet for 20 days. Say hi?" in by[3].why
    assert by[2].following and by[2].score > by[3].score  # moved ranks above quiet


def test_reconnect_includes_people_you_talk_to_but_dont_follow():
    cards = go(talked=collections.Counter({1: 3}), profiles={1: prof(1)})
    assert [c.kind for c in cards] == ["reconnect"]
    assert "You don't follow them yet." in cards[0].why
    assert not cards[0].following


def test_circle_needs_many_mutuals_and_bridge_needs_a_few_plus_somewhere_new():
    sugg = [
        Suggestion(10, 5, followed_by=[1, 2, 3, 4, 5]),  # many mutuals: circle
        Suggestion(11, 2, followed_by=[1, 2]),  # few, other app: bridge
        Suggestion(12, 2, followed_by=[1, 3]),  # few, other channel: bridge
        Suggestion(13, 2, followed_by=[2, 3]),  # few, same app, same place: circle
        Suggestion(14, 2, channels=["https://warpcast.com/~/channel/dev"]),  # channel only: circle
    ]
    profiles = {11: prof(11, now="fireflyapp"), 12: prof(12, channels=["https://warpcast.com/~/channel/art"]),
                13: prof(13, channels=["https://warpcast.com/~/channel/dev"])}
    cards = go(suggestions=sugg, profiles=profiles, my_channels={"https://warpcast.com/~/channel/dev"},
               channel_name=lambda u: "/" + u.rsplit("/", 1)[-1])
    kind = {c.fid: c.kind for c in cards}
    assert kind == {10: "circle", 11: "bridge", 12: "bridge", 13: "circle", 14: "circle"}
    why = {c.fid: " ".join(c.why) for c in cards}
    assert "Followed by 5 people you follow: @alice, @bob, @carol and 2 more." in why[10]
    assert "Posts from fireflyapp; you post from warpcast." in why[11]
    assert "Most active in /art, which you don't post in." in why[12]
    assert "Both active in /dev." in why[14]
    assert CIRCLE_MIN == 4


def test_mix_puts_reconnect_first_then_alternates_circle_and_bridge():
    sugg = [Suggestion(20 + i, 6, followed_by=[1, 2, 3, 4, 5, 6]) for i in range(3)]
    sugg += [Suggestion(30 + i, 2, followed_by=[1, 2]) for i in range(2)]
    profiles = {30: prof(30, now="fireflyapp"), 31: prof(31, now="fireflyapp"), 1: prof(1)}
    cards = go(suggestions=sugg, profiles=profiles, talked=collections.Counter({1: 3}))
    assert [c.kind for c in cards] == ["reconnect", "circle", "bridge", "circle", "bridge", "circle"]


def test_skips_me_followed_dismissed_and_respects_limit():
    sugg = [Suggestion(f, 5, followed_by=[1, 2, 3, 4]) for f in (ME, 1, 40, 41, 42)]
    cards = go(suggestions=sugg, following={1}, dismissed={40}, limit=1)
    assert [c.fid for c in cards] == [41]


def test_own_key_wording_and_unnamed_channels():
    sugg = [Suggestion(50, 2, followed_by=[1, 2])]
    profiles = {50: prof(50, now="own key", channels=["chain://eip155:1/erc721:0xabc"])}
    (card,) = go(suggestions=sugg, profiles=profiles, my_app="warpcast", channel_name=lambda u: u)
    assert "Posts from their own key; you post from warpcast." in card.why
    assert "Most active in a channel you don't post in." in card.why


def test_is_channel():
    assert is_channel("https://warpcast.com/~/channel/dev")
    assert is_channel("chain://eip155:1/erc721:0xabc")
    assert not is_channel("https://en.wikipedia.org/wiki/Anything")
    assert not is_channel(None)
