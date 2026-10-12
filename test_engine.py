"""事件引擎单元测试(不需要游戏、不需要声卡)。运行:python test_engine.py"""
from config import default_config
from engine import OFFLINE, Engine
from gsi_fake import MATE, game, menu

cfg = default_config()
eng = Engine(lambda: cfg)
T = [0.0]


def feed(d, dt=0.0):
    T[0] += dt
    return eng.process(d, T[0])


def tick_until(seconds, hb, step=0.1):
    """推进时间;每 10 秒发一次心跳(与 GSI cfg 的 heartbeat 一致)。"""
    out = []
    end = T[0] + seconds
    next_hb = T[0] + 10
    while T[0] < end:
        T[0] += step
        if T[0] >= next_hb:
            out += eng.process(hb, T[0])
            next_hb += 10
        out += eng.tick(T[0])
    return out


def check(name, got, want):
    ok = got == want
    print(f"{'✓' if ok else '✗'} {name}: {got}" + ("" if ok else f"  (期望 {want})"))
    assert ok


check("启动时在主菜单", feed(menu()), ["menu"])
check("主菜单心跳不重复触发", feed(menu(), 10), [])
check("进入热身", feed(game("warmup", "live"), 5), ["warmup"])
check("热身中阵亡不触发", feed(game("warmup", "live", hp=0), 1), [])
check("热身结束 → 回合开始", feed(game("live", "freezetime"), 5), ["freezetime"])
check("冻结结束 → 行动开始", feed(game("live", "live"), 15), ["round_live"])
# 竞技回合 115s,提前 10s + 0.2s 补偿 → 104.8s 后提示
check("回合剩余10秒(估算)", tick_until(105, game("live", "live")), ["round_10s"])

# 第 2 回合:下包 → 炸弹10秒 → 赢 → MVP(与胜利同一条数据)
check("第2回合开始", feed(game("live", "freezetime", win="CT", ct=1), 10), ["freezetime"])
feed(game("live", "live", ct=1), 15)
check("下包", feed(game("live", "live", bomb="planted", ct=1), 30), ["bomb_planted"])
check("下包后取消回合10秒", eng.round_10s_at, None)
check("炸弹剩余10秒(估算)", tick_until(30, game("live", "live", bomb="planted", ct=1)), ["bomb_10s"])
check("拆包胜利 + MVP 同时到达",
      feed(game("live", "over", bomb="defused", win="CT", ct=2, mvps=1), 8), ["round_won", "mvp"])

# 第 3 回合:阵亡(观察对象切到队友)→ 回合失败;MVP 归别人(看不到自己)
feed(game("live", "freezetime", ct=2, mvps=1), 5)
feed(game("live", "live", ct=2, mvps=1), 15)
check("阵亡:先看到自己 hp=0", feed(game("live", "live", hp=0, ct=2, mvps=1), 20), ["death"])
check("切到观战队友不重复触发", feed(game("live", "live", observe=MATE, ct=2), 2), [])
check("回合失败", feed(game("live", "over", win="T", observe=MATE, ct=2, t=1), 30), ["round_lost"])

# 第 4 回合:直接被切到观战(没收到 hp=0 那一帧)
feed(game("live", "freezetime", ct=2, t=1, mvps=1), 5)
feed(game("live", "live", ct=2, t=1, mvps=1), 15)
check("阵亡:直接切到队友视角", feed(game("live", "live", observe=MATE, ct=2, t=1), 20), ["death"])

# 换边后自己是 T,比赛结束
feed(game("live", "freezetime", team="T", ct=2, t=12, mvps=1), 5)
check("比赛胜利(按我方阵营比分)", feed(game("gameover", "over", win="T", team="T", ct=2, t=13, mvps=1), 60),
      ["match_won", "round_won"])
check("回主菜单", feed(menu(), 20), ["menu"])

# 中途启动程序:第一条数据就是回合进行中且已下包,不应误报
eng2 = Engine(lambda: cfg)
check("中途启动不误报", eng2.process(game("live", "live", bomb="planted", ct=5, t=5), 0), [])
check("中途启动不安排10秒提示", (eng2.round_10s_at, eng2.bomb_10s_at), (None, None))

# 掉线
check("超时视为游戏关闭", eng2.tick(100.0), [OFFLINE])
print("\n全部通过")
