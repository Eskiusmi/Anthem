"""预训练模型 CLAP(Contrastive Language-Audio Pretraining)的语义听感分析。

CLAP 在大量“音频 + 文字描述”上训练,能把一段音乐和一句英文描述放进同一个向量空间,
相似度越高 = 越像这句话描述的音乐。我们用它:
1. 给每首歌每 3 秒算一次 10 秒窗口的音频向量(缓存)
2. 给每个游戏事件写几句“想要的音乐”和“不想要的音乐”描述,算匹配度
3. 给每个段落打情绪标签(史诗 / 激昂 / 紧张 / 悲伤 …)

依赖:torch + transformers(可选安装);权重首次使用时从 HuggingFace 自动下载。
"""
import hashlib
import os
import threading

import numpy as np

DEFAULT_MODEL = "laion/larger_clap_music"   # LAION 专门用音乐训练的 CLAP
CLAP_SR = 48000
WIN = 10.0     # CLAP 输入长度(秒)
HOP = 3.0      # 窗口步长(秒)

# 情绪标签:(中文显示, 英文描述)
TAGS = [
    ("史诗", "epic orchestral cinematic music"),
    ("激昂", "energetic powerful music with heavy drums"),
    ("欢快", "happy upbeat cheerful music"),
    ("胜利", "triumphant victorious celebration music"),
    ("紧张", "tense suspenseful music"),
    ("激烈", "aggressive intense heavy music"),
    ("悲伤", "sad melancholic music"),
    ("平静", "calm peaceful relaxing music"),
    ("梦幻", "dreamy atmospheric ambient music"),
    ("黑暗", "dark ominous music"),
]

# 每个事件:(想要的描述, 不想要的描述)
EVENT_PROMPTS = {
    "mvp": (["an epic triumphant anthem chorus with powerful drums",
             "a victorious celebratory hook, the best part of the song",
             "euphoric energetic music drop"],
            ["calm quiet ambient music", "sad slow music"]),
    "match_won": (["triumphant victorious finale music", "celebration music with a big chorus"],
                  ["sad melancholic music", "calm quiet ambient music"]),
    "round_won": (["upbeat happy energetic music", "short triumphant celebratory music"],
                  ["sad melancholic music", "calm quiet ambient music"]),
    "round_lost": (["sad melancholic music", "disappointed somber music in a minor key"],
                   ["happy upbeat music", "triumphant celebration music"]),
    "match_lost": (["sad emotional ending music", "melancholic somber outro"],
                   ["happy party music", "triumphant celebration music"]),
    "death": (["calm sad ambient music", "quiet emotional piano"],
              ["loud energetic drums", "aggressive intense music"]),
    "bomb_planted": (["tense suspenseful music with a driving rhythm", "dark intense thriller music"],
                     ["happy cheerful music", "calm relaxing music"]),
    "bomb_10s": (["an intense build-up with rising tension before the drop",
                  "fast suspenseful countdown music"],
                 ["calm relaxing music", "slow sad music"]),
    "round_10s": (["an intense build-up with rising tension before the drop",
                   "fast suspenseful countdown music"],
                  ["calm relaxing music", "slow sad music"]),
    "round_live": (["energetic action music with heavy drums", "intense fast-paced battle music"],
                   ["calm quiet music", "slow sad music"]),
    "freezetime": (["steady groovy music while preparing for battle", "moderate tempo background music with a beat"],
                   ["the intense climax of a song", "slow sad music"]),
    "menu": (["chill atmospheric background music", "relaxed lounge music for a menu screen"],
             ["aggressive intense music", "chaotic noisy music"]),
    "warmup": (["upbeat background music for warming up", "groovy electronic music"],
               ["sad slow music", "aggressive intense music"]),
}


def available():
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))


class ClapAI:
    def __init__(self, model_id=DEFAULT_MODEL, log=print):
        self.model_id = model_id or DEFAULT_MODEL
        self.log = log
        self.model = None
        self.proc = None
        self.device = "cpu"
        self.lock = threading.Lock()
        self._text_cache = {}

    # ------------------------------------------------------------ 模型
    def load(self):
        with self.lock:
            if self.model is not None:
                return
            import torch
            from transformers import ClapModel, ClapProcessor
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.log(f"[AI] 加载 CLAP 模型 {self.model_id}(首次会从 HuggingFace 下载,进度见本窗口)…")
            self.proc = ClapProcessor.from_pretrained(self.model_id)
            self.model = ClapModel.from_pretrained(self.model_id).to(self.device).eval()
            ac = self.model.config.audio_config
            self.fusion = bool(getattr(ac, "enable_fusion", False))
            try:
                self.scale = float(self.model.logit_scale_t.exp().item())
            except AttributeError:
                self.scale = 100.0
            self.scale = min(max(self.scale, 10.0), 100.0)
            self.log(f"[AI] CLAP 已就绪(设备:{self.device})")

    @staticmethod
    def _emb(out):
        # transformers 5 返回 BaseModelOutputWithPooling(pooler_output 即投影后的向量);4.x 直接返回张量
        e = out.pooler_output if hasattr(out, "pooler_output") else out
        e = e.detach().float().cpu().numpy()
        return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)

    def text(self, texts):
        missing = [t for t in texts if t not in self._text_cache]
        if missing:
            import torch
            tok = self.proc.tokenizer(missing, padding=True, return_tensors="pt").to(self.device)
            with torch.no_grad():
                e = self._emb(self.model.get_text_features(**tok))
            for t, v in zip(missing, e):
                self._text_cache[t] = v
        return np.stack([self._text_cache[t] for t in texts])

    def audio_windows(self, y, progress=None, batch=8):
        """y: 48kHz 单声道。返回 (窗口数, 维度) 的单位向量,第 k 个窗口从 k*HOP 秒开始。"""
        import torch
        n = int(WIN * CLAP_SR)
        hop = int(HOP * CLAP_SR)
        starts = list(range(0, max(1, len(y) - n + hop), hop)) or [0]
        out = []
        fe = self.proc.feature_extractor
        trunc = "fusion" if self.fusion else "rand_trunc"   # 与模型是否融合版保持一致
        for i in range(0, len(starts), batch):
            chunk = [y[s:s + n] for s in starts[i:i + batch]]
            feats = fe(chunk, sampling_rate=CLAP_SR, truncation=trunc, padding="repeatpad", return_tensors="pt")
            feats = {k: v.to(self.device) for k, v in feats.items()}
            with torch.no_grad():
                out.append(self._emb(self.model.get_audio_features(**feats)))
            if progress:
                progress(min(i + batch, len(starts)), len(starts))
        return np.concatenate(out, axis=0)

    # ------------------------------------------------------------ 缓存
    def song_embeddings(self, path, cache_dir, progress=None):
        st = os.stat(path)
        key = hashlib.sha1(f"{os.path.basename(path)}|{st.st_size}|{int(st.st_mtime)}|{self.model_id}|{HOP}"
                           .encode("utf-8")).hexdigest()[:16]
        cp = os.path.join(cache_dir, f"clap_{key}.npy")
        if os.path.exists(cp):
            return np.load(cp), True
        self.load()
        import librosa
        y, _ = librosa.load(path, sr=CLAP_SR, mono=True)
        E = self.audio_windows(y.astype(np.float32), progress)
        os.makedirs(cache_dir, exist_ok=True)
        np.save(cp, E)
        return E, False

    def event_prompt_embeddings(self):
        self.load()
        out = {}
        for ev, (pos, neg) in EVENT_PROMPTS.items():
            out[ev] = (self.text(pos), self.text(neg))
        tags = self.text([t for _, t in TAGS])
        return out, tags


class SongSemantics:
    """把某首歌的窗口向量组合成任意片段的向量,并算事件匹配度和情绪标签。"""

    def __init__(self, E, prompts, tag_emb, scale):
        self.E = E
        self.prompts = prompts
        self.tag_emb = tag_emb
        self.scale = scale
        self.starts = np.arange(len(E)) * HOP

    def vec(self, start, dur):
        ov = np.clip(np.minimum(self.starts + WIN, start + dur) - np.maximum(self.starts, start), 0, None)
        if ov.sum() <= 0:
            ov = np.zeros(len(self.E))
            ov[int(np.argmin(np.abs(self.starts - start)))] = 1
        v = (self.E * ov[:, None]).sum(axis=0)
        return v / (np.linalg.norm(v) + 1e-9)

    def raw(self, ev, start, dur):
        """想要的描述平均相似度 - 不想要的描述平均相似度。"""
        v = self.vec(start, dur)
        pos, neg = self.prompts[ev]
        return float((pos @ v).mean() - (neg @ v).mean())

    def tags(self, start, dur, k=2):
        v = self.vec(start, dur)
        logits = self.scale * (self.tag_emb @ v)
        p = np.exp(logits - logits.max())
        p /= p.sum()
        idx = np.argsort(-p)[:k]
        return [{"tag": TAGS[i][0], "p": round(float(p[i]), 3)} for i in idx]
