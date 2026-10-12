"""事件引擎:比对前后两次 GSI 状态,生成音乐事件。

纯逻辑,不涉及音频和网络,时间由调用方传入,方便测试。

GSI 要点(以玩家身份游戏时只能拿到这些):
- 没有 "map" 字段 = 在主菜单
- "player" 是你当前观察的玩家;阵亡后会变成你在观战的队友,
  所以用 player.steamid 是否等于 provider.steamid 判断“是不是自己”
- phase_countdowns / bomb 只对观众(GOTV/观战)有效,所以“剩余10秒”只能自己计时估算
"""

OFFLINE = "_offline"  # 内部事件:游戏断开


class Engine:
    def __init__(self, get_config):
        self.get_config = get_config
        self.reset()

    def reset(self):
        self.in_match = None          # None=未知, False=主菜单, True=对局中
        self.map_name = None
        self.mode = None
        self.map_phase = None
        self.round_phase = None
        self.bomb = None
        self.own_team = None
        self.own_alive = None
        self.own_mvps = None
        self.observing_self = None
        self.result_emitted = False
        self.round_10s_at = None
        self.bomb_10s_at = None
        self.last_seen = None
        self.scores = (None, None)

    # ------------------------------------------------------------------ helpers
    def _round_time(self):
        rt = self.get_config().get("round_time", {})
        return float(rt.get(self.mode or "", rt.get("default", 115)) or 0)

    def _lead(self):
        cfg = self.get_config()
        return float(cfg.get("warning_lead", 10)) + float(cfg.get("latency_comp", 0))

    def _new_round(self):
        self.result_emitted = False
        self.round_10s_at = None
        self.bomb_10s_at = None
        self.bomb = None

    # ------------------------------------------------------------------ main
    def process(self, data, now):
        """处理一次 GSI 推送,返回事件 key 列表。"""
        ev = []
        self.last_seen = now
        provider = data.get("provider") or {}
        my_id = str(provider.get("steamid") or "")
        mp = data.get("map")
        rnd = data.get("round") or {}
        pl = data.get("player") or {}

        # ---- 主菜单
        if not mp:
            if self.in_match is not False:
                ev.append("menu")
            self.reset()
            self.in_match = False
            self.last_seen = now
            return ev

        first = self.in_match is not True       # 刚进入对局的第一条数据
        self.in_match = True
        self.map_name = mp.get("name")
        self.mode = mp.get("mode")
        map_phase = mp.get("phase")
        r_phase = rnd.get("phase")
        bomb = rnd.get("bomb")
        warmup = map_phase == "warmup"

        # ---- 观察对象 / 自己的阵营
        pid = str(pl.get("steamid") or "")
        self.observing_self = bool(my_id) and pid == my_id
        state = pl.get("state") or {}
        if self.observing_self and pl.get("team"):
            self.own_team = pl.get("team")

        # ---- 比赛阶段
        if map_phase != self.map_phase:
            if map_phase == "warmup":
                ev.append("warmup")
            elif map_phase == "gameover" and not first:
                ev += self._match_result(mp)
                self.round_10s_at = self.bomb_10s_at = None
            self.map_phase = map_phase

        # ---- 回合阶段
        if r_phase != self.round_phase:
            prev = self.round_phase
            if r_phase == "freezetime":
                self._new_round()
                if not warmup:
                    ev.append("freezetime")
            elif r_phase == "live":
                self.result_emitted = False
                if prev == "freezetime" and not warmup:
                    ev.append("round_live")
                    rt = self._round_time()
                    if rt > 0:
                        self.round_10s_at = now + rt - self._lead()
            elif r_phase == "over":
                self.round_10s_at = self.bomb_10s_at = None
            self.round_phase = r_phase

        # ---- 炸弹
        if bomb != self.bomb:
            if bomb == "planted" and r_phase == "live" and not first and not warmup:
                ev.append("bomb_planted")
                self.round_10s_at = None
                self.bomb_10s_at = now + float(self.get_config().get("bomb_time", 40)) - self._lead()
            elif bomb in ("defused", "exploded"):
                self.bomb_10s_at = None
            self.bomb = bomb

        # ---- 阵亡
        if self.observing_self:
            hp = state.get("health")
            if isinstance(hp, (int, float)):
                alive = hp > 0
                if self.own_alive and not alive and r_phase == "live" and not warmup:
                    ev.append("death")
                self.own_alive = alive
        elif pid:
            # 正在观察别人 → 自己已阵亡或是纯观众
            if self.own_alive and r_phase == "live" and not warmup:
                ev.append("death")
            self.own_alive = False

        # ---- 回合结果
        if r_phase == "over" and not self.result_emitted:
            win = rnd.get("win_team")
            team = self.own_team or pl.get("team")
            if win and team:
                self.result_emitted = True
                if not first and not warmup:
                    ev.append("round_won" if win == team else "round_lost")

        # ---- MVP(只有在观察自己时才能看到自己的 mvps)
        if self.observing_self:
            mvps = (pl.get("match_stats") or {}).get("mvps")
            if isinstance(mvps, int):
                if (self.own_mvps is not None and mvps > self.own_mvps
                        and r_phase == "over" and not warmup):
                    ev.append("mvp")
                self.own_mvps = mvps

        ct = (mp.get("team_ct") or {}).get("score")
        t = (mp.get("team_t") or {}).get("score")
        self.scores = (ct, t)
        return ev

    def _match_result(self, mp):
        team = self.own_team
        ct = (mp.get("team_ct") or {}).get("score")
        t = (mp.get("team_t") or {}).get("score")
        if team not in ("CT", "T") or not isinstance(ct, int) or not isinstance(t, int):
            return []
        mine, other = (ct, t) if team == "CT" else (t, ct)
        if mine > other:
            return ["match_won"]
        if mine < other:
            return ["match_lost"]
        return []

    def tick(self, now):
        """定时调用(约每 0.1 秒),处理估算计时与掉线。"""
        ev = []
        if self.round_10s_at is not None and now >= self.round_10s_at:
            self.round_10s_at = None
            if self.round_phase == "live":
                ev.append("round_10s")
        if self.bomb_10s_at is not None and now >= self.bomb_10s_at:
            self.bomb_10s_at = None
            if self.round_phase == "live":
                ev.append("bomb_10s")
        timeout = float(self.get_config().get("offline_timeout", 35))
        if self.last_seen is not None and now - self.last_seen > timeout:
            self.reset()
            ev.append(OFFLINE)
        return ev

    def snapshot(self, now):
        def remain(t):
            return None if t is None else round(max(0.0, t - now), 1)
        return {
            "connected": self.last_seen is not None,
            "last_seen_ago": None if self.last_seen is None else round(now - self.last_seen, 1),
            "in_match": self.in_match,
            "map": self.map_name,
            "mode": self.mode,
            "map_phase": self.map_phase,
            "round_phase": self.round_phase,
            "bomb": self.bomb,
            "own_team": self.own_team,
            "own_alive": self.own_alive,
            "observing_self": self.observing_self,
            "own_mvps": self.own_mvps,
            "score_ct": self.scores[0],
            "score_t": self.scores[1],
            "round_10s_in": remain(self.round_10s_at),
            "bomb_10s_in": remain(self.bomb_10s_at),
        }
