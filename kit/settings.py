"""统一配置：所有可替换的东西（模型、价格、上限、预算、路径、密钥位置）都从这里读。

优先级（高 → 低）：进程环境变量 > .env 文件 > 本文件的 DEFAULTS。
.env 的位置：环境变量 STUDIO_ENV 指定的路径；否则是 skill 根目录下的 .env（与 SKILL.md 同级）。
没有 .env 也能运行（全部用默认值），但 doctor 会提醒。

密钥：推荐不写进 .env，而是在 CREDENTIALS_FILES 里列出密钥文件的路径（逗号分隔）。
本模块只把密钥放进进程环境变量，不打印、不写入任何文件。

换模型：改 .env 里的 MODEL_*。换价格：改 VIDEO_PRICE_CNY_PER_SECOND。
换供应方：OpenAI 兼容的接口只需改 *_BASE_URL 与密钥；接口形状不同的，在 providers.py 里加一个适配函数。
"""
import os
from pathlib import Path

KIT = Path(__file__).resolve().parent
SKILL_ROOT = KIT.parent
CONFIG_VERSION = 1

DEFAULTS = {
    "CONFIG_VERSION": "1",
    # 数据目录（products/、projects/、ledger/）。留空 = skill 根目录。
    "STUDIO_HOME": "",
    # 调研批次的根目录（相对路径按 STUDIO_HOME 的上一级解析）。
    "RESEARCH_ROOT": "research/tiktok-benchmark",
    "BOARD_RESEARCH": "research/tiktok-benchmark/20261111-loop-test-A",
    # 密钥文件（逗号分隔）。只读取下面 SECRET_KEYS 里的变量。
    "CREDENTIALS_FILES": "~/.config/tokensfactory/credentials.env,~/.config/tikhub/credentials.env",
    # 供应方与地址
    "TEXT_PROVIDER": "tokensfactory",
    "VIDEO_PROVIDER": "tokensfactory",
    "TOKENSFACTORY_BASE_URL": "https://tokensfactory.cc",
    "TIKHUB_BASE_URL": "https://api.tikhub.io",
    "HTTP_USER_AGENT": "TokensFactory-Media/1.1",
    # 模型：MODEL_TEXT 是默认的文本/理解模型；各环节留空则沿用它。
    "MODEL_TEXT": "gemini-3.8-flash",
    "MODEL_VERIFY": "",
    "MODEL_BREAKDOWN": "",
    "MODEL_DRAFT": "",
    "MODEL_CRITIC": "",
    "MODEL_REVIEW": "",
    "MODEL_REVISE": "",
    "MODEL_VIDEO": "minimax-H3",
    # 视频参数与价格
    "VIDEO_PRICE_CNY_PER_SECOND": "0.13",
    "VIDEO_DURATION": "10",
    "VIDEO_RESOLUTION": "768p",
    "VIDEO_ASPECT": "9:16",
    # 上限与预算
    "LIMIT_TIKHUB_CALLS": "100",
    "LIMIT_TEXT_CALLS": "100",
    "PRODUCT_BUDGET_CNY": "5",
    "TIER_WEIGHTS": "H:4,M:3,L:2,X:1",
    "FISSION_MAX_N": "5",
    "REFINE_MAX_ATTEMPTS": "3",
    "REVIEW_FRAMES": "8",
    # 本地视频：github_temp = 上传到下面的公开临时仓库，拆解后删除；off = 禁止上传，只能抽帧
    "MEDIA_UPLOAD": "github_temp",
    "MEDIA_HOST_REPO": "Yeadon8888/studio-media-temp",
}
SECRET_KEYS = ("TOKENSFACTORY_API_KEY", "TIKHUB_API_KEY")
TEXT_ROLES = ("verify", "breakdown", "draft", "critic", "review", "revise")

_cache = None


def parse_env_file(path):
    out = {}
    p = Path(os.path.expanduser(str(path)))
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.replace("export ", "").strip()] = v.strip().strip('"').strip("'")
    return out


def env_path():
    return Path(os.path.expanduser(os.environ.get("STUDIO_ENV") or str(SKILL_ROOT / ".env")))


def load(force=False):
    """读取配置并把密钥放进进程环境（已有的环境变量不覆盖）。返回配置 dict（不含密钥）。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    file_vals = parse_env_file(env_path())
    cfg = dict(DEFAULTS)
    for k, v in file_vals.items():
        if k in SECRET_KEYS:
            os.environ.setdefault(k, v)
        else:
            cfg[k] = v
    for k in list(cfg):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    for f in [x.strip() for x in cfg["CREDENTIALS_FILES"].split(",") if x.strip()]:
        vals = parse_env_file(f)
        for k in SECRET_KEYS:
            if vals.get(k):
                os.environ.setdefault(k, vals[k])
        if vals.get("TOKENSFACTORY_BASE_URL") and "TOKENSFACTORY_BASE_URL" not in file_vals \
                and not os.environ.get("TOKENSFACTORY_BASE_URL"):
            cfg["TOKENSFACTORY_BASE_URL"] = vals["TOKENSFACTORY_BASE_URL"]
    cfg["_env_file"] = str(env_path()) if env_path().is_file() else ""
    _cache = cfg
    return cfg


def get(name):
    return load()[name]


def get_int(name):
    return int(float(get(name)))


def get_float(name):
    return float(get(name))


def model(role="text"):
    """各环节用哪个模型。role: verify/breakdown/draft/critic/review/revise/video/text。"""
    if role == "video":
        return get("MODEL_VIDEO")
    return get(f"MODEL_{role.upper()}") if role in TEXT_ROLES and get(f"MODEL_{role.upper()}") else get("MODEL_TEXT")


def text_models():
    return {model(r) for r in TEXT_ROLES} | {get("MODEL_TEXT")}


def video_price():
    return get_float("VIDEO_PRICE_CNY_PER_SECOND")


def tier_weights():
    out = {}
    for part in get("TIER_WEIGHTS").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = float(v)
    return out


def studio_home():
    v = get("STUDIO_HOME")
    return Path(os.path.expanduser(v)) if v else SKILL_ROOT


def workspace_root():
    """调研批次等相对路径的基准：数据目录的上一级。"""
    return studio_home().parent


def skill_version():
    p = SKILL_ROOT / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0.0.0"


def stamp():
    """写进评审记录与日志的版本戳：之后换了模型或规则，能知道某条结果是用什么跑出来的。"""
    return {"skill": skill_version(), "config": get("CONFIG_VERSION"),
            "models": {"video": model("video"), **{r: model(r) for r in TEXT_ROLES}}}
