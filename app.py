"""
hermes.gg - 롤 전적 검색 서버
"""
import os
import time
import functools
import threading
from flask import Flask, jsonify, request, send_from_directory, Response
import requests as http_requests

app = Flask(__name__, static_folder='.')

CHAMPION_CACHE = None

def load_champions():
    """ddragon 챔피언 id->한글 이름 맵을 서버에서 캐시해 반환 (브라우저 네트워크 의존 제거)"""
    global CHAMPION_CACHE
    if CHAMPION_CACHE:
        return CHAMPION_CACHE
    try:
        vs = http_requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=10).json()
        d = http_requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{vs[0]}/data/ko_KR/champion.json", timeout=15
        ).json()
        CHAMPION_CACHE = {v["key"]: v["name"] for v in d["data"].values()}
    except Exception:
        CHAMPION_CACHE = {}
    return CHAMPION_CACHE

# ============ 보안 강화 ============

# 1) 접근 차단할 민감 파일 (외부 다운로드/열람 방지)
BLOCKED_STATIC = {".riot_key", ".env", "riot.txt", "requirements.txt", "Procfile",
                  "app.py", "server.py", "README.md", "index.html.bak"}
@app.before_request
def block_sensitive_files():
    base = os.path.basename(request.path)
    if request.path.lstrip("/").startswith(".") or base in BLOCKED_STATIC or base.startswith("."):
        return "Forbidden", 403
    return None

# 2) 요청 속도 제한(Rate limit): Riot 키 남용/과다 요청 방지
RATE_MAX, RATE_WINDOW = 20, 10          # 10초당 최대 20회 (IP당)
_rate = {}
_rate_lock = threading.Lock()

def limit_api(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ip = request.remote_addr or "unknown"
        now = time.time()
        with _rate_lock:
            bucket = [t for t in _rate.get(ip, []) if now - t < RATE_WINDOW]
            if len(bucket) >= RATE_MAX:
                _rate[ip] = bucket
                return jsonify({"error": "요청이 너무 많습니다. 잠시 후 다시 시도하세요."}), 429
            bucket.append(now)
            _rate[ip] = bucket
        return fn(*args, **kwargs)
    return wrapper

# 3) 보안 HTTP 헤더
@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    return resp

# 4) Riot API 키 관리: 환경변수(Render 보안 설정) 우선, .riot_key 파일은 최후참조
DOTFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".riot_key")

def get_key():
    env = os.environ.get("RIOT_API_KEY", "").strip()
    if env:
        return env
    if os.path.exists(DOTFILE):
        with open(DOTFILE, "r") as f:
            return f.read().strip()
    return ""

def save_key(key):
    # 로컬 개발용 한정: 환경변수로만 보관(파일 기록 금지), 실행 중에만 유지
    os.environ["RIOT_API_KEY"] = key.strip()

def riot_get(url, key=None):
    k = key or get_key()
    if not k:
        return None, 401
    r = http_requests.get(url, headers={"X-Riot-Token": k}, timeout=10)
    return r.json(), r.status_code

# ============ 페이지 ============

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nAllow: /\nSitemap: https://hermes-gg.onrender.com/sitemap.xml\n",
                    mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap_xml():
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n    <loc>https://hermes-gg.onrender.com/</loc>\n"
        "    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n",
        mimetype="application/xml",
    )

# ============ API ============

@app.route("/api/has-key")
@limit_api
def has_key():
    return jsonify({"hasKey": bool(get_key())})

@app.route("/api/save-key", methods=["POST"])
@limit_api
def api_save_key():
    k = request.get_json(silent=True).get("key", "").strip() if request.get_json(silent=True) else ""
    if not k:
        return jsonify({"ok": False, "msg": "키를 입력하세요"})
    save_key(k)
    return jsonify({"ok": True})

@app.route("/api/account")
@limit_api
def api_account():
    k = get_key()
    if not k:
        return jsonify({"error": "API 키가 설정되지 않았습니다."})
    gn = request.args.get("gameName", "")
    tl = request.args.get("tagLine", "")
    d, c = riot_get(f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gn}/{tl}", k)
    if c != 200:
        return jsonify({"error": f"계정을 찾을 수 없습니다. ({c})"})
    return jsonify(d)

@app.route("/api/ranked/<puuid>")
@limit_api
def api_ranked(puuid):
    k = get_key()
    if not k:
        return jsonify({"error": "API 키 없음"})
    d1, c1 = riot_get(f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}", k)
    if c1 != 200:
        return jsonify({"error": f"소환사 정보 오류 ({c1})", "detail": d1})
    sid = d1.get("id") if "id" in d1 else f"KR1_{puuid}"
    d2, c2 = riot_get(f"https://kr.api.riotgames.com/lol/league/v4/entries/by-summoner/{sid}", k)
    if c2 != 200:
        return jsonify({"error": f"랭크 정보 오류 ({c2})"})
    return jsonify({"summoner": d1, "ranked": d2})

@app.route("/api/matches/<puuid>")
@limit_api
def api_matches(puuid):
    k = get_key()
    if not k:
        return jsonify({"error": "API 키 없음"})
    cnt = request.args.get("count", 20)
    q = request.args.get("queue", 420)
    d, c = riot_get(f"https://asia.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={cnt}&queue={q}", k)
    if c != 200:
        return jsonify({"error": "매치 목록 오류"})
    return jsonify(d)

@app.route("/api/champions")
@limit_api
def api_champions():
    m = load_champions()
    if not m:
        return jsonify({"error": "챔피언 데이터를 불러오지 못했습니다."})
    return jsonify(m)

@app.route("/api/match/<mid>")
@limit_api
def api_match(mid):
    k = get_key()
    if not k:
        return jsonify({"error": "API 키 없음"})
    d, c = riot_get(f"https://asia.api.riotgames.com/lol/match/v5/matches/{mid}", k)
    if c != 200:
        return jsonify({"error": "매치 정보 오류"})
    return jsonify(d)

# ============ 실행 ============

if __name__ == "__main__":
    import webbrowser

    port = int(os.environ.get("PORT", 5000))

    print("=" * 50)
    print("    hermes.gg - 롤 전적 검색")
    print("=" * 50)
    print()
    print("  [OK] 이 서버는 Render(환경변수 키) 배포용입니다.")
    print(f"  서버 주소: http://localhost:{port}")
    print()
    print("  종료: Ctrl+C")
    print("=" * 50)

    if os.environ.get("PORT"):
        print(" Render 배포용: gunicorn 사용 (Procfile 참조)")
    else:
        webbrowser.open(f"http://localhost:{port}")

    app.run(host="0.0.0.0", port=port, debug=False)
