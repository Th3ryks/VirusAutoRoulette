import os
import asyncio
import aiohttp
import json
import re
import hmac
import hashlib
import secrets
from dotenv import load_dotenv
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone
from pyrogram import Client
from pyrogram.raw.functions.messages import RequestAppWebView
from pyrogram.raw.types import InputBotAppShortName, InputUser
from loguru import logger
import sys
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder 
from aiohttp import web
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional

subscribed_channels = {}
accounts_data = {}
bot_instance = None
dp = None

logger.remove()
logger.add(
sys.stdout,
format="| <magenta>{time:YYYY-MM-DD HH:mm:ss}</magenta> | <cyan><level>{level: <8}</level></cyan> | {message}",
level="INFO",
colorize=True,
)

load_dotenv()
graphql_url = "https://virusgift.pro/api/graphql/query"
bot_token = os.getenv("BOT_TOKEN")
admin_id = int(os.getenv("ADMIN_ID"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
COOKIE_ON = os.getenv("COOKIE_ON", "false").strip().lower() in ("1", "true", "yes", "on")
DASHBOARD_PASSWORD = os.getenv("PASSWORD", "")
DASHBOARD_COOKIE_NAME = "vr_dash_auth"
DASHBOARD_COOKIE_MAX_AGE = 10 * 365 * 24 * 3600  # ~10 years; survives script restarts
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
SESSIONS_DIR = Path(__file__).resolve().parent / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def load_account_configs() -> Dict[str, dict]:
    """Load ACCOUNT{N}_API_ID / API_HASH / PHONE_NUMBER from .env."""
    indices = set()
    for key in os.environ:
        match = re.match(r"^ACCOUNT(\d+)_API_ID$", key)
        if match:
            indices.add(int(match.group(1)))

    configs = {}
    for index in sorted(indices):
        api_id = os.getenv(f"ACCOUNT{index}_API_ID")
        api_hash = os.getenv(f"ACCOUNT{index}_API_HASH")
        phone_number = os.getenv(f"ACCOUNT{index}_PHONE_NUMBER")
        if not api_id or not api_hash or not phone_number:
            logger.warning(f"Skipping ACCOUNT{index}: missing API_ID, API_HASH, or PHONE_NUMBER")
            continue
        account_name = f"account{index}"
        configs[account_name] = {
            "api_id": int(api_id),
            "api_hash": api_hash,
            "phone_number": phone_number,
            "session_name": account_name,
        }
    return configs


ACCOUNT_CONFIGS = load_account_configs()
if not ACCOUNT_CONFIGS:
    logger.error("No accounts found in .env (expected ACCOUNT1_API_ID, ACCOUNT1_API_HASH, ACCOUNT1_PHONE_NUMBER, ...)")

@dataclass
class AccountData:
    name: str
    username: str
    balance: int
    next_roulette_time: str
    bearer_token: Optional[str]
    client: Optional[Client]
    subscribed_channels: set
    interacted_bots: set
    next_case_free_spin: str = "Unknown"
    virus_balance: int = 0

class AccountManager:
    def __init__(self):
        self.accounts: Dict[str, AccountData] = {}
        
    async def initialize_account(self, account_name: str, config: dict) -> bool:
        try:
            client = Client(
                config["session_name"],
                config["api_id"],
                config["api_hash"],
                phone_number=config["phone_number"],
                workdir=str(SESSIONS_DIR),
            )
            
            account_data = AccountData(
                name=account_name,
                username="",
                balance=0,
                next_roulette_time="Unknown",
                bearer_token=None,
                client=client,
                subscribed_channels=set(),
                interacted_bots=set(),
                next_case_free_spin="Unknown",
                virus_balance=0,
            )
            
            self.accounts[account_name] = account_data
            return True
            
        except Exception:
            return False
    
    async def get_init_data(self, account_name: str) -> Optional[str]:
        if account_name not in self.accounts:
            return None
            
        client = self.accounts[account_name].client
        if not getattr(client, "is_connected", False):
            logger.error(f"[{account_name}] Client has not been started yet")
            return None
        
        try:
            bot_entity = await client.get_users('virus_play_bot')
            bot = InputUser(user_id=bot_entity.id, access_hash=bot_entity.raw.access_hash)
            peer = await client.resolve_peer('virus_play_bot')
            bot_app = InputBotAppShortName(bot_id=bot, short_name="app")
            web_view = await client.invoke(RequestAppWebView(peer=peer, app=bot_app, platform="android"))
            url_qs = urlparse(web_view.url)
            params = parse_qs(url_qs.query)
            fragment_params = parse_qs(url_qs.fragment)
            init_data = params.get("tgWebAppData", [None])[0]
            if not init_data:
                init_data = fragment_params.get("tgWebAppData", [None])[0]
            return init_data
        except Exception as e:
            logger.error(f"Error getting init data for {account_name}: {e}")
            return None


async def get_next_free_spin_time(bearer_token):
    timers = await get_me_free_timers(bearer_token)
    return timers.get("next_free_spin")


async def get_me_free_timers(bearer_token) -> dict:
    """Fetch nextFreeSpin and nextCaseFreeSpin from me query."""
    query = '''
    query me {
        me {
            nextFreeSpin
            nextCaseFreeSpin
        }
    }
    '''

    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
        'x-batch': 'true',
        'x-timezone': 'Europe/Warsaw',
    }

    json_data = [{
        'operationName': 'me',
        'variables': {},
        'query': query,
    }]

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    result = await response.json()
                    payload = result[0] if isinstance(result, list) and result else result
                    me_data = ((payload or {}).get('data') or {}).get('me') or {}
                    return {
                        'next_free_spin': me_data.get('nextFreeSpin'),
                        'next_case_free_spin': me_data.get('nextCaseFreeSpin'),
                    }
    except Exception:
        pass

    return {'next_free_spin': None, 'next_case_free_spin': None}


def apply_balance_to_account(account_data: AccountData, balance_data) -> None:
    if not isinstance(balance_data, dict):
        return
    account_data.balance = balance_data.get('stars_balance', 0) or 0
    account_data.virus_balance = balance_data.get('virus_balance', 0) or 0


def is_free_reward_ready(next_time: Optional[str]) -> bool:
    """True when timer is missing/unknown/past — reward can be claimed."""
    if not next_time or next_time in ("Unknown", "⏳ Unknown..."):
        return True
    try:
        dt = datetime.fromisoformat(str(next_time).replace('Z', '+00:00'))
        return datetime.now(timezone.utc) >= dt
    except (ValueError, TypeError):
        return True


def build_dashboard_payload() -> dict:
    accounts = []
    for name, acc in account_manager.accounts.items():
        client = acc.client
        online = bool(
            acc.bearer_token
            and client
            and getattr(client, "is_connected", False)
        )
        accounts.append({
            "id": name,
            "username": acc.username or "",
            "stars_balance": acc.balance or 0,
            "virus_balance": getattr(acc, "virus_balance", 0) or 0,
            "next_roulette_time": acc.next_roulette_time,
            "next_case_free_spin": getattr(acc, "next_case_free_spin", None),
            "roulette_ready": is_free_reward_ready(acc.next_roulette_time),
            "case_ready": is_free_reward_ready(getattr(acc, "next_case_free_spin", None)),
            "online": online,
        })
    return {
        "server_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "accounts": accounts,
    }


def _dashboard_cookie_token() -> str:
    """Stable token derived from PASSWORD — valid across process restarts until password changes."""
    return hmac.new(
        DASHBOARD_PASSWORD.encode("utf-8"),
        b"virusroulette-dashboard-auth-v1",
        hashlib.sha256,
    ).hexdigest()


def _dashboard_cookie_ok(request: web.Request) -> bool:
    if not COOKIE_ON:
        return True
    if not DASHBOARD_PASSWORD:
        return False
    raw = request.cookies.get(DASHBOARD_COOKIE_NAME, "")
    if not raw:
        return False
    return secrets.compare_digest(raw, _dashboard_cookie_token())


@web.middleware
async def dashboard_auth_middleware(request: web.Request, handler):
    if not COOKIE_ON:
        return await handler(request)
    if request.path in ("/login", "/api/login", "/site", "/logo.png"):
        return await handler(request)
    if _dashboard_cookie_ok(request):
        return await handler(request)
    if request.path.startswith("/api/"):
        return web.json_response({"error": "Unauthorized"}, status=401)
    raise web.HTTPFound("/login")


async def dashboard_api_accounts(request):
    return web.json_response(build_dashboard_payload())


async def dashboard_api_login(request: web.Request):
    if not COOKIE_ON:
        return web.json_response({"ok": True, "auth": False})
    if not DASHBOARD_PASSWORD:
        return web.json_response({"error": "PASSWORD is not set in .env"}, status=500)
    try:
        body = await request.json()
    except Exception:
        body = {}
    password = str(body.get("password", ""))
    if not secrets.compare_digest(password, DASHBOARD_PASSWORD):
        return web.json_response({"error": "Invalid password"}, status=401)

    resp = web.json_response({"ok": True})
    forwarded = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    resp.set_cookie(
        DASHBOARD_COOKIE_NAME,
        _dashboard_cookie_token(),
        max_age=DASHBOARD_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=(forwarded == "https"),
        path="/",
    )
    return resp


async def dashboard_index(request):
    index_path = DASHBOARD_DIR / "index.html"
    if not index_path.exists():
        return web.Response(text="Dashboard not found", status=404)
    return web.FileResponse(index_path)


async def site_index(request):
    site_path = Path(__file__).resolve().parent / "site" / "index.html"
    if not site_path.exists():
        return web.Response(text="Site not found", status=404)
    return web.FileResponse(site_path)


async def dashboard_logo(request):
    logo_path = Path(__file__).resolve().parent / "logo.png"
    if not logo_path.exists():
        return web.Response(text="Logo not found", status=404)
    return web.FileResponse(logo_path)


async def dashboard_login_page(request):
    if COOKIE_ON and _dashboard_cookie_ok(request):
        raise web.HTTPFound("/")
    login_path = DASHBOARD_DIR / "login.html"
    if not login_path.exists():
        return web.Response(text="Login page not found", status=404)
    return web.FileResponse(login_path)


async def start_dashboard_server():
    if COOKIE_ON and not DASHBOARD_PASSWORD:
        logger.error("COOKIE_ON=true but PASSWORD is empty — dashboard auth will reject everyone")
    middlewares = [dashboard_auth_middleware] if COOKIE_ON else []
    app = web.Application(middlewares=middlewares)
    app.router.add_get("/", dashboard_index)
    app.router.add_get("/site", site_index)
    app.router.add_get("/logo.png", dashboard_logo)
    app.router.add_get("/login", dashboard_login_page)
    app.router.add_post("/api/login", dashboard_api_login)
    app.router.add_get("/api/accounts", dashboard_api_accounts)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, DASHBOARD_HOST, DASHBOARD_PORT)
    await site.start()
    auth_note = " (password auth on)" if COOKIE_ON else ""
    logger.success(f"Dashboard available at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}{auth_note}")

def get_username_from_init_data(init_data):
    try:
        parsed = parse_qs(init_data)
        if 'user' in parsed:
            user_data = json.loads(parsed['user'][0])
            return user_data.get('username', 'Unknown')
    except Exception:
        pass
    return 'Unknown'

async def get_init_data(account_config):
    client = Client(
        account_config["session_name"],
        api_id=account_config["api_id"],
        api_hash=account_config["api_hash"],
        ipv6=False,
        phone_number=account_config["phone_number"],
        workdir=str(SESSIONS_DIR),
    )
    async with client:
        bot_entity = await client.get_users('virus_play_bot')
        bot = InputUser(user_id=bot_entity.id, access_hash=bot_entity.raw.access_hash)
        peer = await client.resolve_peer('virus_play_bot')
        bot_app = InputBotAppShortName(bot_id=bot, short_name="app")
        web_view = await client.invoke(RequestAppWebView(peer=peer, app=bot_app, platform="android"))
        url_qs = urlparse(web_view.url)
        params = parse_qs(url_qs.query)
        fragment_params = parse_qs(url_qs.fragment)
        init_data = params.get("tgWebAppData", [None])[0]
        if not init_data:
            init_data = fragment_params.get("tgWebAppData", [None])[0]
        return init_data

async def subscribe_to_channel(target, account_data: AccountData):
    global subscribed_channels
    max_retries = 3
    base_delay = 2

    if not account_data.client:
        logger.error(f"[{account_data.name}] No active client available for channel subscription")
        return False

    channel_ref = normalize_channel_ref(target)
    if not channel_ref:
        logger.error(f"[{account_data.name}] Empty channel target: {target}")
        return False

    for attempt in range(max_retries):
        try:
            try:
                chat = await account_data.client.join_chat(channel_ref)
            except Exception as join_error:
                if "USER_ALREADY_PARTICIPANT" not in str(join_error):
                    raise
                logger.debug(f"[{account_data.name}] Already subscribed to channel: {channel_ref}")
                chat = await account_data.client.get_chat(channel_ref)

            track_id = getattr(chat, "id", None) or channel_ref
            account_data.subscribed_channels.add(track_id)

            if isinstance(subscribed_channels, dict):
                subscribed_channels[track_id] = True
            else:
                if not isinstance(subscribed_channels, set):
                    subscribed_channels = set()
                subscribed_channels.add(track_id)

            display = getattr(chat, "username", None) or track_id
            logger.info(f"[{account_data.name}] Subscribed to {display} (id={track_id})")
            return True

        except Exception as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"[{account_data.name}] Channel subscription failed: {e}, "
                    f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"[{account_data.name}] Channel subscription failed after all retries: {e}")

    return False


async def unsubscribe_from_channel(account_data: AccountData, channel_ref) -> bool:
    """Leave a channel by chat id, @username, or invite link without raising."""
    if not account_data or not account_data.client or channel_ref is None:
        return False

    client = account_data.client
    if not getattr(client, "is_connected", False):
        logger.error(f"[{account_data.name}] Client is not connected; cannot unsubscribe")
        return False

    leave_target = channel_ref
    if isinstance(channel_ref, str):
        leave_target = normalize_channel_ref(channel_ref) or channel_ref

    try:
        await client.leave_chat(leave_target)
        logger.success(f"[{account_data.name}] Unsubscribed from {leave_target}")
        return True
    except Exception as e:
        error_str = str(e)
        if "USER_NOT_PARTICIPANT" in error_str or "PEER_ID_INVALID" in error_str:
            logger.info(f"[{account_data.name}] Already not in {leave_target}")
            return True
        # Fallback: resolve username/link to chat id, then leave
        try:
            chat = await client.get_chat(leave_target)
            await client.leave_chat(chat.id)
            logger.success(f"[{account_data.name}] Unsubscribed from {getattr(chat, 'username', chat.id)}")
            return True
        except Exception as e2:
            logger.warning(f"[{account_data.name}] Failed to unsubscribe from {leave_target}: {e2}")
            return False


async def unsubscribe_from_channels(account_data, channels_set):
    if not channels_set:
        return

    if isinstance(channels_set, dict):
        channels_list = list(channels_set.keys())
    else:
        channels_list = list(channels_set)

    for channel_ref in channels_list:
        ok = await unsubscribe_from_channel(account_data, channel_ref)
        if ok:
            if isinstance(channels_set, dict):
                channels_set.pop(channel_ref, None)
            elif channel_ref in channels_set:
                channels_set.remove(channel_ref)
        await asyncio.sleep(0.5)

    remaining_count = len(channels_set) if channels_set else 0
    if remaining_count > 0:
        logger.warning(f"[{account_data.name}] Still subscribed to {remaining_count} channels")
    else:
        logger.success(f"[{account_data.name}] Successfully unsubscribed from all channels")


def normalize_channel_ref(target: str):
    """Normalize channel url / @username / invite link for join/leave_chat."""
    if target is None:
        return None
    if isinstance(target, int):
        return target

    value = str(target).strip()
    if not value:
        return None

    if value.startswith("@"):
        return value[1:]

    lower = value.lower()
    if "t.me/" in lower:
        path = value.split("t.me/", 1)[1]
        path = path.split("?")[0].split("#")[0].strip("/")
        if not path:
            return value
        if path.startswith("+") or path.startswith("joinchat/"):
            # Private invite — pass full t.me link or +hash
            if path.startswith("joinchat/"):
                return f"https://t.me/{path}"
            return path if path.startswith("+") else f"+{path}"
        return path.split("/")[0]

    return value


def escape_markdown_v2(text: str) -> str:
    specials = r"_*[]()~`>#+-=|{}.!"
    out = []
    for ch in str(text):
        if ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)

async def get_bearer_token(init_data, account_name=None, ref_code=None):
    json_data = {'operationName': 'authTelegramInitData', 'variables': {'initData': init_data, 'refCode': ref_code}, 'query': 'mutation authTelegramInitData($initData: String!, $refCode: String) { authTelegramInitData(initData: $initData, refCode: $refCode) { token success __typename } }'}
    
    max_retries = 3
    base_delay = 2
    
    for attempt in range(max_retries):
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(graphql_url, json=json_data) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'data' in data and 'authTelegramInitData' in data['data']:
                            auth_data = data['data']['authTelegramInitData']
                            if auth_data.get('success') and 'token' in auth_data:
                                return f"Bearer {auth_data['token']}"
                    elif response.status == 502:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            if account_name:
                                logger.warning(f"[{account_name}] 502 error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                            else:
                                logger.warning(f"502 error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(delay)
                            continue
                    
                    if account_name:
                        logger.error(f"Failed to get bearer token for {account_name}. Status: {response.status}")
                    else:
                        logger.error(f"Failed to get bearer token. Status: {response.status}")
                    
                    if response.status != 502:
                        break
                        
        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Request timeout, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                logger.error("Request timeout after all retries")
        except Exception as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Request error: {e}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Request failed after all retries: {e}")
    
    return None

async def get_account_balance(bearer_token):
    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'apollo-require-preflight': '*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'priority': 'u=1, i',
        'referer': 'https://virusgift.pro/roulette',
        'sec-ch-ua': '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        'x-batch': 'true',
        'x-timezone': 'Europe/Warsaw',
    }
    
    json_data = {
        'operationName': 'me',
        'variables': {},
        'query': 'query me { me { balance starsBalance } }',
    }
    
    max_retries = 3
    base_delay = 1
    
    for attempt in range(max_retries):
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post('https://virusgift.pro/api/graphql/query', headers=headers, json=json_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        payload = result[0] if isinstance(result, list) else result
                        if payload and 'data' in payload and payload['data'] and 'me' in payload['data'] and payload['data']['me']:
                            balance_data = payload['data']['me']
                            return {
                                'virus_balance': balance_data.get('balance', 0),
                                'stars_balance': balance_data.get('starsBalance', 0)
                            }
                        else:
                            logger.warning(f"Invalid balance response structure, attempt {attempt + 1}/{max_retries}")
                    elif response.status == 502:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"Balance 502 error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(delay)
                            continue
                    else:
                        logger.warning(f"Balance API returned status {response.status}, attempt {attempt + 1}/{max_retries}")
                    
                    if response.status != 502 and attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2 ** attempt))
                        
        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Balance request timeout, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                logger.error("Balance request timeout after all retries")
        except Exception as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Balance request error: {e}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Error getting balance after all retries: {e}")
                break
    
    logger.error("Failed to get balance after all retries, returning default values")
    return {'virus_balance': 0, 'stars_balance': 0}

 

async def visit_story_link(link):
    if not link:
        return False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(link, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status == 200
    except Exception:
        return False


async def check_story_post_roulette_prize_win(bearer_token: str, user_prize_id) -> bool:
    """Claim story bonus after a spin. Frontend only opens share UI then calls this —
    actual story publish is not verified by the backend.
    """
    if user_prize_id is None:
        return False
    try:
        uid = int(user_prize_id)
    except (TypeError, ValueError):
        uid = user_prize_id

    headers = {
        "accept": "*/*",
        "authorization": bearer_token,
        "content-type": "application/json",
        "origin": "https://virusgift.pro",
        "referer": "https://virusgift.pro/roulette",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    json_data = {
        "operationName": "checkStoryPostRoulettePrizeWin",
        "variables": {"input": {"userPrizeId": uid}},
        "query": (
            "mutation checkStoryPostRoulettePrizeWin($input: CheckStoryPostRoulettePrizeWinInput!) { "
            "checkStoryPostRoulettePrizeWin(input: $input) { success } }"
        ),
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status != 200:
                    logger.error(f"checkStoryPostRoulettePrizeWin HTTP {response.status}")
                    return False
                result = await response.json()
    except Exception as e:
        logger.error(f"checkStoryPostRoulettePrizeWin failed: {e}")
        return False

    if result.get("errors"):
        for err in result["errors"]:
            code = (err.get("extensions") or {}).get("code", "UNKNOWN")
            logger.warning(f"checkStoryPostRoulettePrizeWin [{code}]: {err.get('message')}")
        return False

    payload = ((result.get("data") or {}).get("checkStoryPostRoulettePrizeWin") or {})
    return bool(payload.get("success"))


def parse_telegram_link(link: str):
    """Parse t.me bot / mini-app deep links."""
    bot_username = None
    short_name = None
    start_param = None

    cleaned = (link or "").strip()
    mini_app_match = re.search(r't\.me/([^/?]+)/([^/?]+)', cleaned)
    if mini_app_match:
        bot_username = mini_app_match.group(1)
        short_name = mini_app_match.group(2)
        # Invite / joinchat are not mini-apps
        if bot_username.startswith('+') or bot_username.lower() == 'joinchat':
            bot_username = None
            short_name = None
    else:
        bot_match = re.search(r't\.me/([^/?]+)', cleaned)
        if bot_match:
            bot_username = bot_match.group(1)
            if bot_username.startswith('+') or bot_username.lower() == 'joinchat':
                bot_username = None

    startapp_match = re.search(r'[?&]startapp=([^&\s#]+)', cleaned, re.IGNORECASE)
    if startapp_match:
        start_param = startapp_match.group(1)
    else:
        start_match = re.search(r'[?&]start=([^&\s#]+)', cleaned)
        if start_match:
            start_param = start_match.group(1)

    return bot_username, short_name, start_param


def infer_test_spin_click_code(link: str = "", message: str = "", error_code: str = None) -> str:
    """Pick the correct markTestSpin* mutation for a partner bot/link."""
    if error_code in {
        'TEST_SPIN_URL_CLICK_REQUIRED',
        'TEST_SPIN_PORTAL_CLICK_REQUIRED',
        'TEST_SPIN_TONNEL_CLICK_REQUIRED',
        'TEST_SPIN_TONPLAY_CLICK_REQUIRED',
    }:
        return error_code

    haystack = f"{link or ''} {message or ''}".lower()
    if 'tonnel' in haystack:
        return 'TEST_SPIN_TONNEL_CLICK_REQUIRED'
    if 'tonplay' in haystack:
        return 'TEST_SPIN_TONPLAY_CLICK_REQUIRED'
    if 'portal' in haystack:
        return 'TEST_SPIN_PORTAL_CLICK_REQUIRED'
    return 'TEST_SPIN_URL_CLICK_REQUIRED'


def graphql_auth_headers(bearer_token: str) -> dict:
    return {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
    }


async def mark_test_spin_click(bearer_token: str, error_code: str, task_id=None) -> bool:
    """Call the official VirusGift mutations used by the website frontend."""
    headers = graphql_auth_headers(bearer_token)
    error_code = infer_test_spin_click_code(error_code=error_code)

    if error_code == 'TEST_SPIN_URL_CLICK_REQUIRED':
        if task_id is None:
            logger.error("TEST_SPIN_URL_CLICK_REQUIRED without task_id")
            return False
        normalized_task_id = int(task_id) if str(task_id).isdigit() else task_id
        payload = {
            'operationName': 'markTestSpinTaskClick',
            'variables': {'taskId': normalized_task_id},
            'query': (
                'mutation markTestSpinTaskClick($taskId: ID!) { '
                'markTestSpinTaskClick(taskId: $taskId) { success } }'
            ),
        }
        result_key = 'markTestSpinTaskClick'
    elif error_code == 'TEST_SPIN_PORTAL_CLICK_REQUIRED':
        payload = {
            'operationName': 'markTestSpinPortalClick',
            'variables': {},
            'query': 'mutation markTestSpinPortalClick { markTestSpinPortalClick { success } }',
        }
        result_key = 'markTestSpinPortalClick'
    elif error_code == 'TEST_SPIN_TONNEL_CLICK_REQUIRED':
        payload = {
            'operationName': 'markTestSpinTonnelClick',
            'variables': {},
            'query': 'mutation markTestSpinTonnelClick { markTestSpinTonnelClick { success } }',
        }
        result_key = 'markTestSpinTonnelClick'
    elif error_code == 'TEST_SPIN_TONPLAY_CLICK_REQUIRED':
        payload = {
            'operationName': 'markTestSpinTonplayClick',
            'variables': {},
            'query': 'mutation markTestSpinTonplayClick { markTestSpinTonplayClick { success } }',
        }
        result_key = 'markTestSpinTonplayClick'
    else:
        logger.error(f"Unsupported test spin click code: {error_code}")
        return False

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(graphql_url, headers=headers, json=payload) as response:
                result = await response.json()
                if response.status != 200:
                    logger.error(f"{result_key} HTTP {response.status}: {result}")
                    return False
                if result.get('errors'):
                    logger.error(f"{result_key} errors: {result['errors']}")
                    return False
                data = (result.get('data') or {}).get(result_key) or {}
                success = bool(data.get('success'))
                if success:
                    logger.success(f"{result_key} succeeded")
                else:
                    logger.error(f"{result_key} returned success=false: {result}")
                return success
    except Exception as e:
        logger.error(f"{result_key} request failed: {e}")
        return False


async def open_telegram_deep_link(account_data, click_link: str) -> bool:
    """Open any t.me mini-app / bot deep link via the logged-in Telegram client."""
    if not account_data or not account_data.client:
        return False

    click_link = (click_link or "").strip()
    if "t.me/" not in click_link:
        return False

    bot_username, short_name, start_param = parse_telegram_link(click_link)
    if not bot_username:
        logger.warning(f"Could not parse bot from link: {click_link}")
        return False

    try:
        bot_entity = await account_data.client.get_users(bot_username)
        bot = InputUser(user_id=bot_entity.id, access_hash=bot_entity.raw.access_hash)
        bot_peer = await account_data.client.resolve_peer(bot_username)
        account_data.interacted_bots.add(bot_username)

        # Mini-app: https://t.me/{bot}/{short_name}?startapp=...
        short_names_to_try = []
        if short_name:
            short_names_to_try.append(short_name)
        elif start_param:
            # Some partners omit short_name in weird links — try common ones
            short_names_to_try.extend(["app", "start", "game", "webapp"])

        for sn in short_names_to_try:
            try:
                web_view = await account_data.client.invoke(
                    RequestAppWebView(
                        peer=bot_peer,
                        app=InputBotAppShortName(bot_id=bot, short_name=sn),
                        platform="android",
                        start_param=start_param or "",
                        write_allowed=True,
                    )
                )
                if web_view and getattr(web_view, "url", None):
                    logger.info(f"Opened mini-app @{bot_username}/{sn} (start_param={start_param})")
                    return True
            except Exception as e:
                logger.debug(f"RequestAppWebView @{bot_username}/{sn} failed: {e}")

        # Regular bot deep link / fallback: /start <param>
        start_command = f"/start {start_param}" if start_param else "/start"
        await account_data.client.send_message(bot_username, start_command)
        logger.info(f"Sent to @{bot_username}: {start_command}")
        return True
    except Exception as e:
        logger.warning(f"Telegram deep link open failed for {click_link}: {e}")
        return False


async def handle_test_spin_click_requirement(
    bearer_token: str,
    error_code: str,
    click_link: str,
    account_data=None,
    task_id=None,
    error_message: str = "",
) -> bool:
    """Mirror website flow: mark click via GraphQL, then open the Telegram link."""
    click_link = (click_link or "").strip().strip("`").strip()
    resolved_code = infer_test_spin_click_code(click_link, error_message, error_code)
    logger.info(
        f"Handling {resolved_code} for link: {click_link}"
        + (f" task_id={task_id}" if task_id is not None else "")
    )

    marked = await mark_test_spin_click(bearer_token, resolved_code, task_id=task_id)
    if not marked:
        # Partner bots without task_id still need the Telegram open for portal/tonnel/tonplay
        if resolved_code == 'TEST_SPIN_URL_CLICK_REQUIRED':
            return False

    opened = await open_telegram_deep_link(account_data, click_link)
    if not opened:
        logger.warning("Mark/open: Telegram deep link open failed; continuing if mark succeeded")

    await asyncio.sleep(2)
    return bool(marked or opened)


# Backward-compatible alias used by older call sites
async def handle_universal_click_requirement(
    bearer_token,
    click_link,
    account_data=None,
    error_code='TEST_SPIN_URL_CLICK_REQUIRED',
    task_id=None,
    error_message: str = "",
):
    return await handle_test_spin_click_requirement(
        bearer_token,
        error_code,
        click_link,
        account_data=account_data,
        task_id=task_id,
        error_message=error_message,
    )


async def auto_visit_telegram_link(
    link,
    bearer_token,
    account_data=None,
    error_code=None,
    task_id=None,
    error_message: str = "",
):
    if not link or 't.me' not in link:
        return False
    resolved = infer_test_spin_click_code(link, error_message, error_code)
    return await handle_test_spin_click_requirement(
        bearer_token,
        resolved,
        link,
        account_data=account_data,
        task_id=task_id,
        error_message=error_message,
    )


async def get_roulette_inventory(bearer_token, user_prize_id):
    headers = {'accept': '*/*', 'authorization': bearer_token, 'content-type': 'application/json', 'origin': 'https://virusgift.pro', 'referer': 'https://virusgift.pro/roulette', 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    json_data = [
        {'operationName': 'claimRoulettePrize', 'variables': {'input': {'userPrizeId': user_prize_id}}, 'query': 'mutation claimRoulettePrize($input: ClaimRoulettePrizeInput!) { claimRoulettePrize(input: $input) { success message telegramGift __typename } }'},
        {'operationName': 'getRouletteInventory', 'variables': {'limit': 10, 'cursor': user_prize_id}, 'query': 'query getRouletteInventory($limit: Int64!, $cursor: Int64!) { getRouletteInventory(cursor: $cursor, limit: $limit) { success prizes { userRoulettePrizeId status prize { id name caption animationUrl photoUrl exchangeCurrency exchangePrice prizeExchangePrice isSpinSellable isClaimable isExchangeable storyLinkAfterWin __typename } claimCost unlockAt __typename } nextCursor hasNextPage __typename } }'}
    ]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list) and len(data) > 0:
                        claim_result = data[0]
                        if 'data' in claim_result and 'claimRoulettePrize' in claim_result['data']:
                            claim_data = claim_result['data']['claimRoulettePrize']
                            if claim_data.get('success'):
                                return {'success': True, 'message': 'Stars claimed successfully'}
                        elif 'errors' in claim_result:
                            return {'success': False, 'errors': claim_result['errors']}
                    return data
        except Exception:
            pass
    return None

async def start_roulette_spin(bearer_token):
    headers = {'accept': '*/*', 'authorization': bearer_token, 'content-type': 'application/json', 'origin': 'https://virusgift.pro', 'referer': 'https://virusgift.pro/roulette', 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    json_data = {'operationName': 'startRouletteSpin', 'variables': {'input': {'type': 'X1'}}, 'query': 'mutation startRouletteSpin($input: StartRouletteSpinInput!) { startRouletteSpin(input: $input) { success prize { id name caption animationUrl photoUrl exchangeCurrency exchangePrice prizeExchangePrice isSpinSellable isClaimable isExchangeable storyLinkAfterWin __typename } userPrizeId balance isStoryRewardAvailable storyReward __typename } }'}
    
    max_retries = 3
    base_delay = 2
    
    for attempt in range(max_retries):
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(graphql_url, headers=headers, json=json_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        if 'errors' in result:
                            for error in result['errors']:
                                error_code = error.get('extensions', {}).get('code', 'UNKNOWN')
                                error_message = error.get('message', 'Unknown error')
                                logger.error(f"Roulette API error [{error_code}]: {error_message}")
                                return result
                        
                        if 'data' in result and result['data'] and 'startRouletteSpin' in result['data']:
                            spin_data = result['data']['startRouletteSpin']
                            if spin_data and spin_data.get('success', False):
                                logger.debug("Roulette spin successful")
                                return result
                            else:
                                logger.warning("Roulette spin not successful")
                                return result
                        else:
                            logger.warning("Invalid roulette response structure")
                            return result
                            
                    elif response.status == 502:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"Roulette 502 error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(delay)
                            continue
                    else:
                        response_text = await response.text()
                        logger.error(f"Roulette API returned status {response.status}: {response_text}")
                    
                    if response.status != 502:
                        break
                        
        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Roulette request timeout, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                logger.error("Roulette request timeout after all retries")
        except Exception as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Roulette request error: {e}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Error in roulette spin request: {e}")
    
    logger.error("Roulette spin failed after all retries")
    return None


async def get_free_cases(bearer_token):
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette/cases',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    }
    json_data = {
        'operationName': 'cases',
        'variables': {},
        'query': (
            'query cases { cases { success cases { id name type starsPrice animationUrl expiresAt '
            'prizes { id animationUrl starsAmount } } } }'
        ),
    }
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status != 200:
                    return []
                result = await response.json()
                cases = (((result or {}).get('data') or {}).get('cases') or {}).get('cases') or []
                return [c for c in cases if str(c.get('type', '')).upper() == 'FREE']
    except Exception as e:
        logger.error(f"Failed to fetch cases: {e}")
        return []


async def open_case(bearer_token, case_id, demo: bool = False):
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette/cases',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    }
    json_data = {
        'operationName': 'openCase',
        'variables': {'id': str(case_id), 'demo': bool(demo)},
        'query': (
            'mutation openCase($id: ID!, $demo: Boolean!) { openCase(id: $id, demo: $demo) { '
            'success prize { id name caption animationUrl photoUrl exchangeCurrency exchangePrice '
            'prizeExchangePrice isSpinSellable isClaimable isExchangeable } '
            'userPrizeId casePrizeId demo } }'
        ),
    }
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    return await response.json()
                logger.error(f"openCase HTTP {response.status}: {await response.text()}")
    except Exception as e:
        logger.error(f"openCase request failed: {e}")
    return None


async def resolve_action_errors(account_name: str, account_data: AccountData, result, retry_callable):
    """Handle click / subscription errors and retry the GraphQL action."""
    click_codes = {
        'TEST_SPIN_URL_CLICK_REQUIRED',
        'TEST_SPIN_PORTAL_CLICK_REQUIRED',
        'TEST_SPIN_TONNEL_CLICK_REQUIRED',
        'TEST_SPIN_TONPLAY_CLICK_REQUIRED',
    }

    for _attempt in range(5):
        if not result or 'errors' not in result:
            return result, None

        handled = False
        for error in result['errors']:
            error_code = error.get('extensions', {}).get('code', 'UNKNOWN')
            error_message = error.get('message', 'Unknown error')
            extensions = error.get('extensions', {}) or {}

            if error_code == 'INSUFFICIENT_BALANCE':
                return result, 'INSUFFICIENT_BALANCE'

            if error_code in click_codes:
                click_link = extensions.get('link')
                task_id = extensions.get('task_id')
                if not click_link:
                    logger.error(f"[{account_name}] {error_code} without link")
                    return result, error_code
                click_link = click_link.strip().strip('`').strip()
                logger.info(f"[{account_name}] Click required ({error_code}): {click_link}")
                click_success = await handle_test_spin_click_requirement(
                    account_data.bearer_token,
                    error_code,
                    click_link,
                    account_data=account_data,
                    task_id=task_id,
                    error_message=error_message,
                )
                if not click_success:
                    return result, error_code
                await asyncio.sleep(3)
                result = await retry_callable()
                handled = True
                break

            if error_code == 'TELEGRAM_SUBSCRIPTION_REQUIRED':
                target = extensions.get('url') or extensions.get('username')
                if not target:
                    logger.error(f"[{account_name}] Subscription required but no channel provided")
                    return result, error_code
                ok = await subscribe_to_channel(target, account_data)
                if not ok:
                    return result, error_code
                logger.success(f"[{account_name}] Successfully subscribed to {target}")
                await asyncio.sleep(2)
                result = await retry_callable()
                handled = True
                break

            logger.error(f"[{account_name}] API error [{error_code}]: {error_message}")

        if not handled:
            return result, 'UNHANDLED'

    return result, 'RETRIES_EXHAUSTED'


async def process_account_free_case(account_name: str, account_data: AccountData) -> bool:
    """Open the daily FREE case when nextCaseFreeSpin allows it."""
    try:
        if not is_free_reward_ready(getattr(account_data, 'next_case_free_spin', None)):
            return False

        logger.info(f"[{account_name}] Checking free case...")
        free_cases = await get_free_cases(account_data.bearer_token)
        if not free_cases:
            logger.warning(f"[{account_name}] No FREE cases available")
            timers = await get_me_free_timers(account_data.bearer_token)
            if timers.get('next_case_free_spin'):
                account_data.next_case_free_spin = timers['next_case_free_spin']
            return False

        case = free_cases[0]
        case_id = case.get('id')
        case_name = case.get('name') or case_id
        logger.info(f"[{account_name}] Opening free case: {case_name} (id={case_id})")

        result = await open_case(account_data.bearer_token, case_id, demo=False)
        result, stop_reason = await resolve_action_errors(
            account_name,
            account_data,
            result,
            lambda: open_case(account_data.bearer_token, case_id, demo=False),
        )

        open_data = ((result or {}).get('data') or {}).get('openCase') or {}
        if not open_data.get('success'):
            if stop_reason == 'INSUFFICIENT_BALANCE':
                logger.info(f"[{account_name}] Free case not available yet (balance/cooldown)")
            else:
                logger.error(f"[{account_name}] Free case failed ({stop_reason}): {(result or {}).get('errors')}")
            timers = await get_me_free_timers(account_data.bearer_token)
            if timers.get('next_case_free_spin') is not None:
                account_data.next_case_free_spin = timers['next_case_free_spin'] or account_data.next_case_free_spin
            return False

        prize = open_data.get('prize') or {}
        prize_info = prize.get('name') or prize.get('caption') or 'Unknown prize'
        user_prize_id = open_data.get('userPrizeId')
        logger.success(f"[{account_name}] Free case opened: {prize_info}")

        timers = await get_me_free_timers(account_data.bearer_token)
        if timers.get('next_case_free_spin') is not None:
            account_data.next_case_free_spin = timers['next_case_free_spin']
            logger.info(f"[{account_name}] Next free case at: {account_data.next_case_free_spin}")

        claim_note = ""
        if user_prize_id and (
            prize.get('isClaimable')
            or prize_currency_kind(prize)
            or (isinstance(prize_info, str) and ('stars' in prize_info.lower() or 'virus' in prize_info.lower()))
        ):
            claimed = await collect_currency_prize(
                account_data.bearer_token,
                user_prize_id,
                prize=prize,
                account_data=account_data,
                account_name=account_name,
            )
            claim_note = " (claimed)" if claimed else " (claim failed)"

        # Sweep inventory for any leftover Virus/Stars prizes
        await check_and_claim_rewards(account_data.bearer_token, account_data)

        balance_result = await get_account_balance(account_data.bearer_token)
        stars_balance = balance_result.get('stars_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
        virus_balance = balance_result.get('virus_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
        if isinstance(balance_result, dict):
            apply_balance_to_account(account_data, balance_result)

        message = (
            f"> @{account_data.username} opened free case\n"
            f"🎁 Case: {case_name}\n"
            f"⭐ Prize: {prize_info}{claim_note}\n"
            f"💰 Stars Balance: {await format_number_with_spaces(stars_balance)}\n"
            f"💰 Virus Balance: {await format_number_with_spaces(virus_balance)}"
        )
        await send_notification(message)

        try:
            await cleanup_after_reward(account_data)
        except Exception as e:
            logger.error(f"[{account_name}] Cleanup after free case failed: {e}")

        return True
    except Exception as e:
        logger.error(f"[{account_name}] Error in free case process: {e}")
        return False


async def get_inventory_prizes(bearer_token):
    headers = {'accept': '*/*', 'authorization': bearer_token, 'content-type': 'application/json', 'origin': 'https://virusgift.pro', 'referer': 'https://virusgift.pro/roulette', 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    json_data = {'operationName': 'getRouletteInventory', 'variables': {'limit': 50, 'cursor': 0}, 'query': 'query getRouletteInventory($limit: Int64!, $cursor: Int64!) { getRouletteInventory(cursor: $cursor, limit: $limit) { success prizes { userRoulettePrizeId status prize { id name caption animationUrl photoUrl exchangeCurrency exchangePrice prizeExchangePrice isSpinSellable isClaimable isExchangeable storyLinkAfterWin __typename } claimCost unlockAt __typename } nextCursor hasNextPage __typename } }'}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    return await response.json()
        except Exception:
            pass
    return None

async def claim_prize(bearer_token, user_prize_id):
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    }
    uid = int(user_prize_id) if str(user_prize_id).isdigit() else user_prize_id
    json_data = {
        'operationName': 'claimRoulettePrize',
        'variables': {'input': {'userPrizeId': uid}},
        'query': (
            'mutation claimRoulettePrize($input: ClaimRoulettePrizeInput!) { '
            'claimRoulettePrize(input: $input) { success message telegramGift __typename } }'
        ),
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    return await response.json()
                logger.error(f"claimRoulettePrize HTTP {response.status}: {await response.text()}")
        except Exception as e:
            logger.error(f"claimRoulettePrize request failed: {e}")
    return None


async def exchange_prize_to_stars(bearer_token, user_prize_id, price=None):
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    }
    uid = int(user_prize_id) if str(user_prize_id).isdigit() else user_prize_id
    inp = {'userPrizeId': uid}
    if price is not None:
        inp['price'] = price
    json_data = {
        'operationName': 'exchangeRoulettePrizeToStarsBalance',
        'variables': {'input': inp},
        'query': (
            'mutation exchangeRoulettePrizeToStarsBalance($input: ExchangeRoulettePrizeToStarsBalanceInput!) { '
            'exchangeRoulettePrizeToStarsBalance(input: $input) { success } }'
        ),
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    return await response.json()
                logger.error(f"exchangeRoulettePrizeToStarsBalance HTTP {response.status}: {await response.text()}")
        except Exception as e:
            logger.error(f"exchangeRoulettePrizeToStarsBalance request failed: {e}")
    return None


def prize_currency_kind(prize: dict) -> Optional[str]:
    """Return 'virus', 'stars', or None for non-currency (gift) prizes."""
    prize = prize or {}
    name = str(prize.get('name') or prize.get('caption') or '').lower()
    if 'virus' in name:
        return 'virus'
    if 'star' in name:
        return 'stars'
    currency = str(prize.get('exchangeCurrency') or '').upper()
    if currency == 'VIRUS':
        return 'virus'
    if currency == 'STARS':
        return 'stars'
    return None


async def collect_currency_prize(
    bearer_token,
    user_prize_id,
    prize=None,
    account_data=None,
    account_name: str = "",
) -> bool:
    """Collect non-gift Virus/Stars rewards to balance (with click/sub retries)."""
    if not user_prize_id:
        return False

    prize = prize or {}
    kind = prize_currency_kind(prize)
    label = account_name or (account_data.name if account_data else "account")
    await asyncio.sleep(1)

    should_claim = bool(kind) or bool(prize.get('isClaimable', True if kind else False))
    if should_claim or kind:
        async def do_claim():
            return await claim_prize(bearer_token, user_prize_id)

        result = await do_claim()
        if account_data is not None:
            result, _stop = await resolve_action_errors(label, account_data, result, do_claim)

        payload = ((result or {}).get('data') or {}).get('claimRoulettePrize') or {}
        if payload.get('success'):
            logger.success(f"[{label}] Prize claimed to balance (userPrizeId={user_prize_id})")
            return True

        errors = (result or {}).get('errors') or []
        if errors:
            code = (errors[0].get('extensions') or {}).get('code', 'UNKNOWN')
            msg = errors[0].get('message', 'Unknown error')
            logger.error(f"[{label}] claimRoulettePrize failed [{code}]: {msg}")
        elif result is None:
            logger.error(f"[{label}] claimRoulettePrize returned empty response")
        else:
            logger.error(f"[{label}] claimRoulettePrize success=false: {result}")

    sellable = prize.get('isSpinSellable') or prize.get('isExchangeable') or kind == 'stars'
    price = prize.get('prizeExchangePrice')
    if price is None:
        price = prize.get('exchangePrice')

    if sellable or kind == 'stars':
        async def do_exchange():
            return await exchange_prize_to_stars(bearer_token, user_prize_id, price=price)

        result = await do_exchange()
        if account_data is not None:
            result, _stop = await resolve_action_errors(label, account_data, result, do_exchange)

        payload = ((result or {}).get('data') or {}).get('exchangeRoulettePrizeToStarsBalance') or {}
        if payload.get('success'):
            logger.success(f"[{label}] Prize exchanged to stars (userPrizeId={user_prize_id})")
            return True

        errors = (result or {}).get('errors') or []
        if errors:
            code = (errors[0].get('extensions') or {}).get('code', 'UNKNOWN')
            msg = errors[0].get('message', 'Unknown error')
            logger.error(f"[{label}] exchange to stars failed [{code}]: {msg}")
            if code == 'EXCHANGE_PRICE_CHANGED':
                new_price = (errors[0].get('extensions') or {}).get('currentPrice')
                if new_price is not None:
                    result = await exchange_prize_to_stars(bearer_token, user_prize_id, price=new_price)
                    payload = ((result or {}).get('data') or {}).get('exchangeRoulettePrizeToStarsBalance') or {}
                    if payload.get('success'):
                        logger.success(f"[{label}] Prize exchanged to stars after price update")
                        return True

    return False


async def check_and_claim_rewards(bearer_token, account_data=None):
    """Claim all Virus/Stars currency prizes sitting in roulette inventory."""
    inventory_result = await get_inventory_prizes(bearer_token)

    if not inventory_result:
        return False

    if 'errors' in inventory_result:
        for error in inventory_result.get('errors', []):
            if error.get('extensions', {}).get('code') == 'UNAUTHORIZED':
                return None
        return False

    inventory = ((inventory_result.get('data') or {}).get('getRouletteInventory') or {})
    if not inventory.get('success'):
        return False

    prizes = inventory.get('prizes') or []
    account_name = account_data.name if account_data else "account"
    rewards_found = False

    for prize_data in prizes:
        prize = prize_data.get('prize') or {}
        prize_name = str(prize.get('name') or prize.get('caption') or '')
        user_prize_id = prize_data.get('userRoulettePrizeId')
        unlock_at = prize_data.get('unlockAt')
        kind = prize_currency_kind(prize)

        if not user_prize_id or not kind:
            continue

        if unlock_at:
            try:
                unlock_dt = datetime.fromisoformat(str(unlock_at).replace('Z', '+00:00'))
                if unlock_dt > datetime.now(timezone.utc):
                    logger.info(f"[{account_name}] Prize {prize_name} locked until {unlock_at}")
                    continue
            except (ValueError, TypeError):
                pass

        logger.info(f"[{account_name}] Collecting inventory prize: {prize_name} (id={user_prize_id})")
        ok = await collect_currency_prize(
            bearer_token,
            user_prize_id,
            prize=prize,
            account_data=account_data,
            account_name=account_name,
        )
        if ok:
            rewards_found = True
        await asyncio.sleep(0.5)

    if rewards_found and account_data:
        try:
            await cleanup_after_reward(account_data)
        except Exception as e:
            logger.error(f"[{account_name}] Cleanup after inventory claim failed: {e}")

    return rewards_found

async def cleanup_after_reward(account_data: AccountData):
    if not account_data or not account_data.client:
        return

    if account_data.subscribed_channels:
        logger.info(
            f"[{account_data.name}] Unsubscribing from {len(account_data.subscribed_channels)} channel(s) after spin..."
        )
        await unsubscribe_from_channels(account_data, account_data.subscribed_channels)

    if account_data.interacted_bots:
        logger.info(f"[{account_data.name}] Deleting {len(account_data.interacted_bots)} bot chats...")
        for bot_username in list(account_data.interacted_bots):
            try:
                await account_data.client.delete_chat_history(bot_username, revoke=True)
                logger.success(f"Deleted chat history with bot: {bot_username}")
                account_data.interacted_bots.discard(bot_username)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"Failed to delete chat with {bot_username}: {e}")

async def refresh_bearer_token(account_config, account_data=None):
    max_retries = 3
    
    if not account_data or not getattr(account_data, "client", None):
        logger.error("Account data or client is missing for token refresh")
        return None
    
    client = account_data.client
    if not getattr(client, "is_connected", False):
        logger.error(f"[{account_data.name}] Client is not connected; cannot refresh token during runtime")
        return None
    
    for attempt in range(max_retries):
        try:
            bot_entity = await client.get_users('virus_play_bot')
            bot = InputUser(user_id=bot_entity.id, access_hash=bot_entity.raw.access_hash)
            peer = await client.resolve_peer('virus_play_bot')
            bot_app = InputBotAppShortName(bot_id=bot, short_name="app")
            web_view = await client.invoke(RequestAppWebView(peer=peer, app=bot_app, platform="android"))
            url_qs = urlparse(web_view.url)
            params = parse_qs(url_qs.query)
            fragment_params = parse_qs(url_qs.fragment)
            init_data = params.get("tgWebAppData", [None])[0]
            if not init_data:
                init_data = fragment_params.get("tgWebAppData", [None])[0]
            
            if not init_data:
                logger.warning(f"Attempt {attempt + 1}/{max_retries}: Failed to get init_data")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                    continue
                return None
            
            bearer_token = await get_bearer_token(init_data, account_data.name)
            if not bearer_token:
                logger.warning(f"Attempt {attempt + 1}/{max_retries}: Failed to get bearer_token")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                    continue
                return None
            
            is_valid = await validate_bearer_token(bearer_token, account_data.name)
            if is_valid:
                logger.success(f"[{account_data.name}] Bearer token refreshed successfully on attempt {attempt + 1}")
                return bearer_token
            else:
                logger.warning(f"[{account_data.name}] Attempt {attempt + 1}/{max_retries}: New token validation failed")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                    continue
        except Exception as e:
            logger.error(f"[{account_data.name}] Attempt {attempt + 1}/{max_retries}: Token refresh error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
                continue
    
    logger.error(f"[{account_data.name}] Failed to refresh bearer token after all attempts")
    return None

async def handle_token_refresh_and_retry(bearer_token, func, *args, **kwargs):
    result = await func(bearer_token, *args, **kwargs)
    
    if result is None or (isinstance(result, dict) and 'errors' in result):
        if isinstance(result, dict) and 'errors' in result:
            for error in result.get('errors', []):
                if error.get('extensions', {}).get('code') == 'UNAUTHORIZED':
                    logger.warning("Token expired, refreshing...")
                
                    logger.error("Token refresh requires account configuration")
                    return None, None
    
    return bearer_token, result


async def validate_bearer_token(bearer_token, account_name="Unknown"):
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    
    json_data = {
        'operationName': 'me',
        'variables': {},
        'query': 'query me { me { starsBalance nextFreeSpin } }'
    }
    
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post('https://virusgift.pro/api/graphql/query', headers=headers, json=json_data) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    if 'errors' in result:
                        for error in result.get('errors', []):
                            if error.get('extensions', {}).get('code') == 'UNAUTHORIZED':
                                logger.error(f"[{account_name}] Bearer token is INVALID - UNAUTHORIZED")
                                return False
                            else:
                                logger.warning(f"[{account_name}] API error: {error}")
                        return False
                    
                    if 'data' in result and result['data'] and 'me' in result['data']:
                        me_data = result['data']['me']
                        if me_data is not None:
                            balance = me_data.get('starsBalance', 'Unknown')
                            next_spin = me_data.get('nextFreeSpin', 'Unknown')
                            logger.debug(f"[{account_name}] Bearer token is VALID - Balance: {balance}, Next spin: {next_spin}")
                            return True
                        else:
                            logger.error(f"[{account_name}] Bearer token is INVALID - null user data")
                            return False
                    else:
                        logger.error(f"[{account_name}] Bearer token is INVALID - no user data")
                        return False
                        
                elif response.status == 401:
                    logger.error(f"[{account_name}] Bearer token is INVALID - 401 Unauthorized")
                    return False
                else:
                    response_text = await response.text()
                    logger.error(f"[{account_name}] Token validation failed - Status {response.status}: {response_text}")
                    return False
                    
    except asyncio.TimeoutError:
        logger.error(f"[{account_name}] Token validation timeout")
        return False
    except Exception as e:
        logger.error(f"[{account_name}] Token validation error: {e}")
        return False

async def validate_all_tokens_from_accounts(account_manager):
    logger.info("Starting bearer token validation for all accounts...")
    
    invalid_tokens = []
    valid_tokens = []
    
    for account_name, account_data in account_manager.accounts.items():
        if account_data.bearer_token:
            is_valid = await validate_bearer_token(account_data.bearer_token, account_name)
            if is_valid:
                valid_tokens.append(account_name)
            else:
                invalid_tokens.append(account_name)
        else:
            logger.warning(f"[{account_name}] No bearer token found")
            invalid_tokens.append(account_name)
    
    logger.info(f"Token validation complete - Valid: {len(valid_tokens)}, Invalid: {len(invalid_tokens)}")
    
    if invalid_tokens:
        logger.error(f"Accounts with invalid tokens: {', '.join(invalid_tokens)}")
    
    if valid_tokens:
        logger.debug(f"Accounts with valid tokens: {', '.join(valid_tokens)}")
    
    return valid_tokens, invalid_tokens

async def wait_for_next_spin(next_spin_time):
    if not next_spin_time:
        return False
    
    try:
        target_time = datetime.fromisoformat(next_spin_time.replace('Z', '+00:00'))
        
        while True:
            current_time = datetime.now(timezone.utc)
            
            if current_time >= target_time:
                logger.info("Free spin time reached, waiting 1 second...")
                await asyncio.sleep(1)
                return True
            
            time_diff = (target_time - current_time).total_seconds()
            hours = int(time_diff // 3600)
            minutes = int((time_diff % 3600) // 60)
            seconds = int(time_diff % 60)
            
            if time_diff > 60:
                logger.info(f"Next free spin in {hours:02d}:{minutes:02d}:{seconds:02d}")
                await asyncio.sleep(30)
            else:
                logger.info(f"Next free spin in {hours:02d}:{minutes:02d}:{seconds:02d}")
                await asyncio.sleep(min(time_diff, 10))
                
    except Exception as e:
        logger.error(f"Error waiting for next spin: {e}")
        return False


async def get_main_menu_text(account_manager: AccountManager) -> str:
    text = "*🦠 VIRUS ROULETTE SPINNER 🦠*\n\n"

    if not account_manager.accounts:
        text += escape_markdown_v2("No accounts loaded.")
        return text

    for account_name, account_data in account_manager.accounts.items():
        balance = account_data.balance if account_data.balance else 0
        formatted_balance = await format_number_with_spaces(balance)
        username = account_data.username if account_data.username else account_name

        next_time = account_data.next_roulette_time
        if next_time and next_time != "Unknown" and next_time != "⏳ Unknown...":
            try:
                dt = datetime.fromisoformat(next_time.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                diff = dt - now

                if diff.total_seconds() <= 0:
                    time_display = "Ready"
                else:
                    hours = int(diff.total_seconds() // 3600)
                    minutes = int((diff.total_seconds() % 3600) // 60)

                    if hours > 0:
                        time_display = f"{hours}h {minutes}m"
                    else:
                        time_display = f"{minutes}m"
            except (ValueError, TypeError):
                time_display = "unknown"
        else:
            time_display = "unknown"

        safe_user = str(username).replace("`", "")
        safe_balance = escape_markdown_v2(str(formatted_balance))
        safe_time = str(time_display).replace("`", "")

        case_time = getattr(account_data, "next_case_free_spin", None)
        if is_free_reward_ready(case_time):
            case_display = "Ready"
        elif case_time and case_time != "Unknown":
            try:
                dt = datetime.fromisoformat(str(case_time).replace('Z', '+00:00'))
                diff = dt - datetime.now(timezone.utc)
                if diff.total_seconds() <= 0:
                    case_display = "Ready"
                else:
                    hours = int(diff.total_seconds() // 3600)
                    minutes = int((diff.total_seconds() % 3600) // 60)
                    case_display = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
            except (ValueError, TypeError):
                case_display = "unknown"
        else:
            case_display = "unknown"
        safe_case = str(case_display).replace("`", "")

        text += (
            f"`@{safe_user}` \\- ⭐️*{safe_balance}* \\- 🕐 `{safe_time}` "
            f"\\- 🎁 `{safe_case}`\n"
        )


    return text

async def get_main_menu_keyboard() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()
    return builder.as_markup()


async def setup_bot_handlers():

    @dp.message(Command("start"))
    async def start_command(message: types.Message):
        try:
            if message.from_user.id != admin_id:
                await message.answer("Access denied")
                return

            user_id = message.from_user.id
            username = message.from_user.username or "No Username"
            first_name = message.from_user.first_name or "No Name"
            logger.success(f"Sent welcome message to user {user_id} | @{username} | {first_name}")

            update_tasks = []
            for account_name, account_data in account_manager.accounts.items():
                if account_data.bearer_token:
                    update_tasks.append(update_single_account_status(account_name, account_data))

            if update_tasks:
                await asyncio.gather(*update_tasks, return_exceptions=True)

            text = await get_main_menu_text(account_manager)
            keyboard = await get_main_menu_keyboard()
            try:
                await message.answer(text, reply_markup=keyboard, parse_mode="MarkdownV2")
            except Exception as parse_error:
                logger.warning(f"MarkdownV2 send failed, fallback to plain text: {parse_error}")
                plain = re.sub(r"[\\*_`]", "", text)
                await message.answer(plain, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"/start handler error: {e}")
            try:
                await message.answer("Bot error while building status. Check logs.")
            except Exception:
                pass

async def update_single_account_status(account_name, account_data):
    try:
        balance_data = await get_account_balance(account_data.bearer_token)
        apply_balance_to_account(account_data, balance_data)

        timers = await get_me_free_timers(account_data.bearer_token)
        if timers.get('next_free_spin') is not None:
            account_data.next_roulette_time = timers['next_free_spin']
        if timers.get('next_case_free_spin') is not None:
            account_data.next_case_free_spin = timers['next_case_free_spin']
    except Exception as e:
        logger.error(f"Failed to update {account_name}: {e}")


account_manager = AccountManager()

async def update_all_accounts_status():
    for account_name, account_data in account_manager.accounts.items():
        if account_data.bearer_token:
            try:
                balance_data = await get_account_balance(account_data.bearer_token)
                apply_balance_to_account(account_data, balance_data)

                timers = await get_me_free_timers(account_data.bearer_token)
                if timers.get('next_free_spin') is not None:
                    account_data.next_roulette_time = timers['next_free_spin']
                if timers.get('next_case_free_spin') is not None:
                    account_data.next_case_free_spin = timers['next_case_free_spin']
            except Exception as e:
                logger.error(f"Failed to update {account_name}: {e}")

async def process_account_roulette(account_name: str, account_data: AccountData):
    try:
        logger.info(f"[{account_name}] Starting roulette spin...")
        
        is_token_valid = await validate_bearer_token(account_data.bearer_token, account_name)
        if not is_token_valid:
            config = ACCOUNT_CONFIGS.get(account_name)
            if config:
                new_token = await refresh_bearer_token(config, account_data)
                if new_token:
                    account_data.bearer_token = new_token
                else:
                    return
            else:
                return

        # Claim daily free case in the same run as roulette
        if is_free_reward_ready(getattr(account_data, 'next_case_free_spin', None)):
            try:
                await process_account_free_case(account_name, account_data)
            except Exception as e:
                logger.error(f"[{account_name}] Free case during roulette run failed: {e}")
        
        result = await start_roulette_spin(account_data.bearer_token)

        click_codes = {
            'TEST_SPIN_URL_CLICK_REQUIRED',
            'TEST_SPIN_PORTAL_CLICK_REQUIRED',
            'TEST_SPIN_TONNEL_CLICK_REQUIRED',
            'TEST_SPIN_TONPLAY_CLICK_REQUIRED',
        }

        for _attempt in range(5):
            if not result or 'errors' not in result:
                break

            handled = False
            for error in result['errors']:
                error_code = error.get('extensions', {}).get('code', 'UNKNOWN')
                error_message = error.get('message', 'Unknown error')
                extensions = error.get('extensions', {}) or {}

                if error_code == 'INSUFFICIENT_BALANCE':
                    logger.error(f"[{account_name}] Roulette spin failed - insufficient balance")
                    next_time = await get_next_free_spin_time(account_data.bearer_token)
                    if next_time:
                        account_data.next_roulette_time = next_time
                        logger.info(f"[{account_name}] Updated next roulette time to: {next_time}")
                    else:
                        from datetime import timedelta
                        next_time = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace('+00:00', 'Z')
                        account_data.next_roulette_time = next_time
                        logger.info(f"[{account_name}] Set next roulette time to 24h from now: {next_time}")
                    return False

                if error_code in click_codes:
                    click_link = extensions.get('link')
                    task_id = extensions.get('task_id')
                    if not click_link:
                        logger.error(f"[{account_name}] {error_code} without link")
                        return False
                    click_link = click_link.strip().strip('`').strip()
                    logger.info(f"[{account_name}] Click required ({error_code}): {click_link}")
                    click_success = await handle_test_spin_click_requirement(
                        account_data.bearer_token,
                        error_code,
                        click_link,
                        account_data=account_data,
                        task_id=task_id,
                    )
                    if not click_success:
                        logger.error(f"[{account_name}] Test spin click mark failed")
                        return False
                    logger.info(f"[{account_name}] Waiting 3 seconds after click mark...")
                    await asyncio.sleep(3)
                    result = await start_roulette_spin(account_data.bearer_token)
                    handled = True
                    break

                if error_code == 'TELEGRAM_SUBSCRIPTION_REQUIRED':
                    username = extensions.get('username')
                    url = extensions.get('url')
                    target = url or username
                    if not target:
                        logger.error(f"[{account_name}] Subscription required but no channel provided")
                        return False
                    subscription_success = await subscribe_to_channel(target, account_data)
                    if not subscription_success:
                        logger.error(f"[{account_name}] Failed to subscribe to {target}")
                        return False
                    logger.success(f"[{account_name}] Successfully subscribed to {target}")
                    await asyncio.sleep(2)
                    result = await start_roulette_spin(account_data.bearer_token)
                    handled = True
                    break

                if error_code != 'TELEGRAM_SUBSCRIPTION_REQUIRED':
                    logger.error(f"Roulette API error [{error_code}]: {error_message}")

            if not handled:
                break
        
        if result is None:
            config = ACCOUNT_CONFIGS[account_name]
            new_bearer_token = await refresh_bearer_token(config, account_data)
            if new_bearer_token:
                account_data.bearer_token = new_bearer_token
                result = await start_roulette_spin(account_data.bearer_token)
            else:
                return False
        
        if result and 'data' in result and result['data'] and 'startRouletteSpin' in result['data']:
            spin_data = result['data']['startRouletteSpin']
            if spin_data and spin_data.get('success', False):
                logger.success(f"[{account_name}] Roulette spin completed successfully")

                # Lock next free-spin time immediately so a post-spin crash
                # cannot make the worker pay for another spin.
                next_time = await get_next_free_spin_time(account_data.bearer_token)
                if next_time:
                    account_data.next_roulette_time = next_time
                    logger.info(f"[{account_name}] Next free spin at: {next_time}")

                try:
                    prize_info = "Unknown prize"
                    user_prize_id = spin_data.get('userPrizeId')
                    success_message = ""

                    prize_data = spin_data.get('prize') or {}
                    if prize_data.get('name') is not None:
                        prize_info = prize_data.get('name')
                    elif prize_data.get('caption') is not None:
                        prize_info = prize_data.get('caption')
                    if isinstance(prize_info, (int, float)):
                        prize_info = str(prize_info)

                    balance_result = await get_account_balance(account_data.bearer_token)
                    virus_balance = balance_result.get('virus_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                    stars_balance = balance_result.get('stars_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                    formatted_virus_balance = await format_number_with_spaces(virus_balance)
                    formatted_stars_balance = await format_number_with_spaces(stars_balance)

                    claim_ok = False
                    if user_prize_id and (
                        prize_currency_kind(prize_data)
                        or (isinstance(prize_info, str) and ('stars' in prize_info.lower() or 'virus' in prize_info.lower()))
                        or prize_data.get('isClaimable')
                    ):
                        claim_ok = await collect_currency_prize(
                            account_data.bearer_token,
                            user_prize_id,
                            prize=prize_data,
                            account_data=account_data,
                            account_name=account_name,
                        )
                        if claim_ok:
                            balance_result = await get_account_balance(account_data.bearer_token)
                            virus_balance = balance_result.get('virus_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                            stars_balance = balance_result.get('stars_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                            formatted_virus_balance = await format_number_with_spaces(virus_balance)
                            formatted_stars_balance = await format_number_with_spaces(stars_balance)
                            success_message = (
                                f"> @{account_data.username} successfully spun and claimed the roulette\n"
                                f"⭐ Prize: {prize_info}\n"
                                f"💰 Stars Balance: {formatted_stars_balance}\n"
                                f"💰 Virus Balance: {formatted_virus_balance}"
                            )
                            logger.success(
                                f"[{account_name}] Prize claimed: {prize_info} | Stars: {stars_balance} | Virus: {virus_balance}"
                            )
                        else:
                            success_message = (
                                f"> @{account_data.username} successfully spun the roulette\n"
                                f"⭐ Prize: {prize_info} (failed to claim)\n"
                                f"💰 Stars Balance: {formatted_stars_balance}\n"
                                f"💰 Virus Balance: {formatted_virus_balance}"
                            )
                    else:
                        success_message = (
                            f"> @{account_data.username} received a prize\n"
                            f"🎁 Prize: {prize_info}\n"
                            f"💰 Stars Balance: {formatted_stars_balance}\n"
                            f"💰 Virus Balance: {formatted_virus_balance}"
                        )

                    # Story bonus: site opens share UI then calls this mutation (~5s later).
                    # Backend does not require an actual Telegram story post.
                    if user_prize_id and spin_data.get("isStoryRewardAvailable"):
                        story_amount = spin_data.get("storyReward") or 0
                        logger.info(
                            f"[{account_name}] Story reward available ({story_amount}) — claiming via checkStoryPostRoulettePrizeWin"
                        )
                        await asyncio.sleep(2)
                        story_ok = await check_story_post_roulette_prize_win(
                            account_data.bearer_token, user_prize_id
                        )
                        if not story_ok:
                            await asyncio.sleep(4)
                            story_ok = await check_story_post_roulette_prize_win(
                                account_data.bearer_token, user_prize_id
                            )
                        if story_ok:
                            logger.success(
                                f"[{account_name}] Story reward claimed (userPrizeId={user_prize_id}, amount={story_amount})"
                            )
                            balance_result = await get_account_balance(account_data.bearer_token)
                            if isinstance(balance_result, dict):
                                apply_balance_to_account(account_data, balance_result)
                            await check_and_claim_rewards(account_data.bearer_token, account_data)
                        else:
                            logger.warning(f"[{account_name}] Story reward claim failed")

                    # Collect any leftover Virus/Stars from inventory
                    await check_and_claim_rewards(account_data.bearer_token, account_data)

                    if success_message:
                        await send_notification(success_message)

                    if isinstance(balance_result, dict):
                        apply_balance_to_account(account_data, balance_result)

                    await update_all_accounts_status()
                except Exception as e:
                    logger.error(f"[{account_name}] Post-spin handling failed (spin already counted): {e}")

                # Always leave required channels after a successful spin
                try:
                    await cleanup_after_reward(account_data)
                except Exception as e:
                    logger.error(f"[{account_name}] Error during post-spin unsubscribe: {e}")

                return True
            else:
                logger.warning(f"[{account_name}] Roulette spin failed or not ready yet")
                return False
        else:
            if result and 'errors' in result:
                logger.error(f"[{account_name}] Roulette spin failed with errors: {result['errors']}")
            else:
                logger.error(f"[{account_name}] Roulette spin failed - invalid response structure")
            return False
            
    except Exception as e:
        logger.error(f"[{account_name}] Error in roulette process: {e}")
        return False

async def send_notification(message: str):
    try:
        logger.info(f"Sending notification: {message[:50]}...")
        escaped_message = message.replace('.', '\\.')
        escaped_message = escaped_message.replace('-', '\\-')
        escaped_message = escaped_message.replace('!', '\\!')
        escaped_message = escaped_message.replace('(', '\\(')
        escaped_message = escaped_message.replace(')', '\\)')
        escaped_message = escaped_message.replace('_', '\\_')
        escaped_message = escaped_message.replace('*', '\\*')
        escaped_message = escaped_message.replace('[', '\\[')
        escaped_message = escaped_message.replace(']', '\\]')
        escaped_message = escaped_message.replace('~', '\\~')
        escaped_message = escaped_message.replace('`', '\\`')
        escaped_message = escaped_message.replace('>', '\\>')
        escaped_message = escaped_message.replace('#', '\\#')
        escaped_message = escaped_message.replace('+', '\\+')
        escaped_message = escaped_message.replace('=', '\\=')
        escaped_message = escaped_message.replace('|', '\\|')
        escaped_message = escaped_message.replace('{', '\\{')
        escaped_message = escaped_message.replace('}', '\\}')
        await bot_instance.send_message(admin_id, escaped_message, parse_mode="MarkdownV2")
        return True
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return False

async def calculate_hours_until_roulette(next_roulette_time: str) -> int:
    try:
        if next_roulette_time and next_roulette_time != "Unknown":
            dt = datetime.fromisoformat(next_roulette_time.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            diff = dt - now
            return max(0, int(diff.total_seconds() / 3600))
    except (ValueError, TypeError):
        pass
    return 0

async def calculate_minutes_until_roulette(next_roulette_time: str) -> int:
    try:
        if next_roulette_time and next_roulette_time != "Unknown":
            dt = datetime.fromisoformat(next_roulette_time.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            diff = dt - now
            return max(0, int(diff.total_seconds() / 60))
    except (ValueError, TypeError):
        pass
    return 0

async def format_number_with_spaces(number):
    if isinstance(number, str) and number.isdigit():
        number = int(number)
    if isinstance(number, (int, float)):
        return f"{number:,}".replace(",", " ")
    return str(number)

async def notification_scheduler():
    last_notified_hours = {}
    
    while True:
        try:
            current_time = datetime.now(timezone.utc)
        
            if current_time.minute == 0 and current_time.second < 60:
                for account_name, account_data in account_manager.accounts.items():
                    if account_data.next_roulette_time and account_data.next_roulette_time != "Unknown":
                        hours_left = await calculate_hours_until_roulette(account_data.next_roulette_time)
                        minutes_left = await calculate_minutes_until_roulette(account_data.next_roulette_time)
                        
                
                        if hours_left >= 1 and hours_left <= 23:
                            key = f"{account_name}_{hours_left}h"
                            if key not in last_notified_hours:
                                message = f"⏰ *Countdown Alert*\n\n`@{account_data.username}` \- {hours_left} hours left until next roulette\!"
                                await send_notification(message)
                                last_notified_hours[key] = True
                        
                
                        if hours_left == 0 and minutes_left == 0:
                            keys_to_remove = [k for k in last_notified_hours.keys() if k.startswith(account_name)]
                            for key in keys_to_remove:
                                del last_notified_hours[key]
            
        
            elif current_time.second < 60:
                for account_name, account_data in account_manager.accounts.items():
                    if account_data.next_roulette_time and account_data.next_roulette_time != "Unknown":
                        minutes_left = await calculate_minutes_until_roulette(account_data.next_roulette_time)
                        
                
                        if minutes_left == 30 and current_time.second == 0:
                            key = f"{account_name}_30m"
                            if key not in last_notified_hours:
                                message = f"⏰ *Final Countdown*\n\n`@{account_data.username}` \- 30 minutes left until roulette\!"
                                await send_notification(message)
                                last_notified_hours[key] = True
                        
                
                        elif minutes_left == 5 and current_time.second == 0:
                            key = f"{account_name}_5m"
                            if key not in last_notified_hours:
                                message = f"🚨 *Last Call*\n\n`@{account_data.username}` \- Only 5 minutes left\!"
                                await send_notification(message)
                                last_notified_hours[key] = True
            
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Error in notification scheduler: {e}")
            await asyncio.sleep(60)

async def main():
    global bot_instance, dp
    
    logger.success("Account initializing started...")
    

    bot_instance = Bot(token=bot_token)
    dp = Dispatcher()
    

    await setup_bot_handlers()
    

    token_tasks = []
    
    for account_name, config in ACCOUNT_CONFIGS.items():
        await initialize_account_client(account_name, config, account_manager)
    
    for account_name, config in ACCOUNT_CONFIGS.items():
        if account_name in account_manager.accounts:
            token_tasks.append(get_account_token_and_username(account_name, config, account_manager))
    
    await asyncio.gather(*token_tasks)
    
    update_tasks = []
    for account_name, account_data in account_manager.accounts.items():
        if account_data.bearer_token:
            update_tasks.append(update_single_account_status(account_name, account_data))
    
    if update_tasks:
        await asyncio.gather(*update_tasks, return_exceptions=True)

    # On startup: collect Virus/Stars prizes left in inventory
    for account_name, account_data in account_manager.accounts.items():
        if not account_data.bearer_token:
            continue
        try:
            logger.info(f"[{account_name}] Checking inventory for unclaimed Virus/Stars...")
            claimed = await check_and_claim_rewards(account_data.bearer_token, account_data)
            if claimed:
                balance_data = await get_account_balance(account_data.bearer_token)
                if isinstance(balance_data, dict):
                    apply_balance_to_account(account_data, balance_data)
                    logger.success(
                        f"[{account_name}] Inventory collected | Stars: {balance_data.get('stars_balance')} | "
                        f"Virus: {balance_data.get('virus_balance')}"
                    )
            else:
                logger.info(f"[{account_name}] No claimable Virus/Stars in inventory")
        except Exception as e:
            logger.error(f"[{account_name}] Startup inventory claim failed: {e}")

    await start_dashboard_server()

    async def roulette_worker():
        while True:
            try:
                for account_name, account_data in account_manager.accounts.items():
                    if not account_data.bearer_token:
                        continue
                    try:
                        roulette_ready = is_free_reward_ready(account_data.next_roulette_time)
                        case_ready = is_free_reward_ready(getattr(account_data, 'next_case_free_spin', None))

                        if roulette_ready:
                            success = await process_account_roulette(account_name, account_data)
                            timers = await get_me_free_timers(account_data.bearer_token)
                            if timers.get('next_free_spin') is not None:
                                account_data.next_roulette_time = timers['next_free_spin']
                            if timers.get('next_case_free_spin') is not None:
                                account_data.next_case_free_spin = timers['next_case_free_spin']
                            if success is False and timers.get('next_free_spin'):
                                account_data.next_roulette_time = timers['next_free_spin']
                        elif case_ready:
                            await process_account_free_case(account_name, account_data)
                            timers = await get_me_free_timers(account_data.bearer_token)
                            if timers.get('next_case_free_spin') is not None:
                                account_data.next_case_free_spin = timers['next_case_free_spin']
                    except Exception as e:
                        logger.error(f"[{account_name}] Error in roulette worker: {e}")

                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error in roulette worker: {e}")
                await asyncio.sleep(20)
    
    
    asyncio.create_task(roulette_worker())
    

    logger.success("TG Bot started successfully...")
    await dp.start_polling(bot_instance)

async def initialize_account_client(account_name, config, account_manager):
    try:
        api_id = config.get("api_id")
        api_hash = config.get("api_hash")
        session_name = config.get("session_name")

        if not session_name:
            logger.error(f"[{account_name}] Missing session_name in configuration")
            return False

        session_file = SESSIONS_DIR / f"{session_name}.session"

        if not api_id or not api_hash:
            logger.error(
                f"[{account_name}] API credentials missing. Set {account_name.upper()}_API_ID and {account_name.upper()}_API_HASH in .env"
            )
            return False

        success = await account_manager.initialize_account(account_name, config)
        if not success:
            logger.error(f"[{account_name}] Failed to initialize account")
            return False

        try:
            await account_manager.accounts[account_name].client.start()
            logger.success(f"[{account_name}] Client started; session ready: {session_file}")
            return True
        except Exception as e:
            logger.error(f"[{account_name}] Failed to start client: {e}")
            try:
                del account_manager.accounts[account_name]
            except Exception:
                pass
            return False
    except Exception as e:
        logger.error(f"[{account_name}] Unexpected error during initialization: {e}")
        return False

async def get_account_token_and_username(account_name, config, account_manager):
    if account_name not in account_manager.accounts:
        return False
        
    try:
        client = account_manager.accounts[account_name].client
        if not getattr(client, "is_connected", False):
            logger.error(f"[{account_name}] Client has not been started yet")
            return False

        init_data = await account_manager.get_init_data(account_name)
        if init_data:
            bearer_token = await get_bearer_token(init_data, account_name)
            if bearer_token:
                account_manager.accounts[account_name].bearer_token = bearer_token
                
                username = get_username_from_init_data(init_data)
                account_manager.accounts[account_name].username = username
                
                logger.success(f"[{account_name}] Authentication successful - {username}")
                return True
            else:
                logger.error(f"[{account_name}] Failed to get bearer token")
        else:
            logger.error(f"[{account_name}] Failed to get init data")
    except Exception as e:
        logger.error(f"[{account_name}] Error during authentication: {e}")
    return False

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Shutdown requested by Ctrl+C")
        try:
            async def shutdown():
                try:
                    for account_name, account_data in account_manager.accounts.items():
                        try:
                            if account_data.client:
                                await account_data.client.stop()
                                logger.info(f"[{account_name}] Client stopped")
                        except Exception:
                            pass
                    if bot_instance:
                        try:
                            await bot_instance.session.close()
                        except Exception:
                            pass
                except Exception:
                    pass
            asyncio.run(shutdown())
        except Exception:
            pass
