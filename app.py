"""
hermes.gg - 롤 전적 검색 서버
"""
import os
from flask import Flask, jsonify, request, send_from_directory
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

API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".riot_key")

def get_key():
    if os.path.exists(API_KEY_FILE):
        with open(API_KEY_FILE,"r") as f:
            return f.read().strip()
    return os.environ.get("RIOT_API_KEY","")

def save_key(key):
    with open(API_KEY_FILE,"w") as f:
        f.write(key.strip())

def riot_get(url, key=None):
    k = key or get_key()
    if not k: return None, 401
    r = http_requests.get(url, headers={"X-Riot-Token": k}, timeout=10)
    return r.json(), r.status_code

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/riot.txt")
def riot_txt():
    return send_from_directory(".", "riot.txt", mimetype="text/plain")

@app.route("/api/has-key")
def has_key():
    return jsonify({"hasKey": bool(get_key())})

@app.route("/api/save-key", methods=["POST"])
def api_save_key():
    k = request.json.get("key","").strip()
    if not k: return jsonify({"ok":False,"msg":"키를 입력하세요"})
    save_key(k)
    return jsonify({"ok":True})

@app.route("/api/account")
def api_account():
    k = get_key()
    if not k: return jsonify({"error":"API 키가 설정되지 않았습니다."})
    gn = request.args.get("gameName","")
    tl = request.args.get("tagLine","")
    d, c = riot_get(f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gn}/{tl}", k)
    if c != 200: return jsonify({"error":f"계정을 찾을 수 없습니다. ({c})"})
    return jsonify(d)

@app.route("/api/ranked/<puuid>")
def api_ranked(puuid):
    k = get_key()
    if not k: return jsonify({"error":"API 키 없음"})
    d1, c1 = riot_get(f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}", k)
    if c1 != 200: return jsonify({"error":f"소환사 정보 오류 ({c1})", "detail": d1})
    sid = d1.get("id") if "id" in d1 else f"KR1_{puuid}"
    d2, c2 = riot_get(f"https://kr.api.riotgames.com/lol/league/v4/entries/by-summoner/{sid}", k)
    if c2 != 200: return jsonify({"error":f"랭크 정보 오류 ({c2})"})
    return jsonify({"summoner":d1,"ranked":d2})

@app.route("/api/matches/<puuid>")
def api_matches(puuid):
    k = get_key()
    if not k: return jsonify({"error":"API 키 없음"})
    cnt = request.args.get("count",20)
    q = request.args.get("queue",420)
    d, c = riot_get(f"https://asia.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?count={cnt}&queue={q}", k)
    if c != 200: return jsonify({"error":"매치 목록 오류"})
    return jsonify(d)

@app.route("/api/champions")
def api_champions():
    m = load_champions()
    if not m:
        return jsonify({"error":"챔피언 데이터를 불러오지 못했습니다."})
    return jsonify(m)

@app.route("/api/match/<mid>")
def api_match(mid):
    k = get_key()
    if not k: return jsonify({"error":"API 키 없음"})
    d, c = riot_get(f"https://asia.api.riotgames.com/lol/match/v5/matches/{mid}", k)
    if c != 200: return jsonify({"error":"매치 정보 오류"})
    return jsonify(d)

if __name__ == "__main__":
    import webbrowser, time
    from threading import Timer

    port = int(os.environ.get("PORT", 5000))

    print("=" * 50)
    print("    hermes.gg - 롤 전적 검색")
    print("=" * 50)
    print()

    has = bool(get_key())
    if has:
        print("  [OK] API 키 로드 완료")
    else:
        print("  [!] API 키가 없습니다. 브라우저에서 설정해주세요.")

    print()
    print(f"  서버 주소: http://localhost:{port}")
    print()

    def open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")
    Timer(2.0, open_browser).start()

    print("  종료: Ctrl+C")
    print("=" * 50)

    app.run(host="0.0.0.0", port=port, debug=False)
