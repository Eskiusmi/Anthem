"""构造假的 GSI 数据,供测试和模拟器使用(字段结构与 CS2 推送一致)。"""

ME = "76561198000000001"
MATE = "76561198000000002"


def menu():
    return {"provider": {"name": "Counter-Strike 2", "appid": 730, "steamid": ME},
            "player": {"steamid": ME, "name": "Tim", "activity": "menu"}}


def game(map_phase="live", r_phase="freezetime", bomb=None, win=None,
         observe=ME, team="CT", hp=100, mvps=0, ct=0, t=0, mode="competitive"):
    rnd = {"phase": r_phase}
    if bomb:
        rnd["bomb"] = bomb
    if win:
        rnd["win_team"] = win
    return {
        "provider": {"name": "Counter-Strike 2", "appid": 730, "steamid": ME},
        "map": {"mode": mode, "name": "de_mirage", "phase": map_phase, "round": ct + t,
                "team_ct": {"score": ct}, "team_t": {"score": t}},
        "round": rnd,
        "player": {"steamid": observe, "name": "Tim" if observe == ME else "Mate",
                   "team": team, "activity": "playing",
                   "state": {"health": hp, "armor": 100},
                   "match_stats": {"kills": 0, "assists": 0, "deaths": 0, "mvps": mvps, "score": 0}},
    }


def demo_script():
    """一整局的演示:(说明, GSI 数据, 停留秒数)。“剩余10秒”这类倒计时要真实等待,演示里不包含。"""
    return [
        ("主菜单", menu(), 4),
        ("热身", game("warmup", "live"), 4),
        ("第1回合 冻结时间", game("live", "freezetime"), 4),
        ("行动开始", game("live", "live"), 4),
        ("下包", game("live", "live", bomb="planted"), 5),
        ("拆包胜利 + 拿到 MVP", game("live", "over", bomb="defused", win="CT", ct=1, mvps=1), 6),
        ("第2回合 冻结时间", game("live", "freezetime", ct=1, mvps=1), 4),
        ("行动开始", game("live", "live", ct=1, mvps=1), 3),
        ("阵亡", game("live", "live", hp=0, ct=1, mvps=1), 2),
        ("观战队友", game("live", "live", observe=MATE, ct=1), 3),
        ("回合失败", game("live", "over", win="T", observe=MATE, ct=1, t=1), 5),
        ("比赛结束(我方领先)", game("gameover", "over", win="CT", ct=13, t=1, mvps=1), 6),
        ("回到主菜单", menu(), 3),
    ]
