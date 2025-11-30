import os
import asyncio
import aiohttp
import json
import re
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
logger.add(
    "bot.log",
    format="| {time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="INFO",
    rotation="10 MB",
    retention="7 days",
    compression="zip",
)

load_dotenv()
graphql_url = "https://virusgift.pro/api/graphql/query"
bot_token = os.getenv("BOT_TOKEN")
admin_id = int(os.getenv("ADMIN_ID"))

ACCOUNT_CONFIGS = {
    "account1": {
        "api_id": os.getenv("ACCOUNT1_API_ID"),
        "api_hash": os.getenv("ACCOUNT1_API_HASH"),
        "phone_number": os.getenv("ACCOUNT1_PHONE_NUMBER"),
        "session_name": "account1"
    },
    "account2": {
        "api_id": os.getenv("ACCOUNT2_API_ID"),
        "api_hash": os.getenv("ACCOUNT2_API_HASH"),
        "phone_number": os.getenv("ACCOUNT2_PHONE_NUMBER"),
        "session_name": "account2"
    },
    "account3": {
        "api_id": os.getenv("ACCOUNT3_API_ID"),
        "api_hash": os.getenv("ACCOUNT3_API_HASH"),
        "phone_number": os.getenv("ACCOUNT3_PHONE_NUMBER"),
        "session_name": "account3"
    },
    "account4": {
        "api_id": os.getenv("ACCOUNT4_API_ID"),
        "api_hash": os.getenv("ACCOUNT4_API_HASH"),
        "phone_number": os.getenv("ACCOUNT4_PHONE_NUMBER"),
        "session_name": "account4"
    }
}

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

class AccountManager:
    def __init__(self):
        self.accounts: Dict[str, AccountData] = {}
        
    async def initialize_account(self, account_name: str, config: dict) -> bool:
        try:
            client = Client(
                config["session_name"],
                config["api_id"],
                config["api_hash"],
                phone_number=config["phone_number"]
            )
            
            account_data = AccountData(
                name=account_name,
                username="",
                balance=0,
                next_roulette_time="Unknown",
                bearer_token=None,
                client=client,
                subscribed_channels=set(),
                interacted_bots=set()
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
    query = '''
    query me {
        me {
            nextFreeSpin
        }
    }
    '''
    
    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'apollo-require-preflight': '*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'priority': 'u=1, i',
        'referer': 'https://virusgift.pro/',
        'sec-ch-ua': '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-storage-access': 'active',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
        'x-batch': 'true',
        'x-timezone': 'Europe/Warsaw'
    }
    
    json_data = [{
        'operationName': 'me',
        'variables': {},
        'query': query
    }]
    
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post('https://virusgift.pro/api/graphql/query', headers=headers, json=json_data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result and len(result) > 0 and 'data' in result[0] and result[0]['data'] and 'me' in result[0]['data']:
                        me_data = result[0]['data']['me']
                        if me_data and 'nextFreeSpin' in me_data:
                            return me_data['nextFreeSpin']
    except Exception:
        pass
    
    return None

async def get_portal_link(bearer_token):
    query = '''
    query {
        getPortalInfo {
            link
            active
        }
    }
    '''
    
    headers = {
        'authorization': bearer_token,
        'content-type': 'application/json'
    }
    
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(graphql_url, headers=headers, json={'query': query}) as response:
                if response.status == 200:
                    result = await response.json()
                    if result and 'data' in result and result['data'] and 'getPortalInfo' in result['data']:
                        portal_info = result['data']['getPortalInfo']
                        if portal_info and portal_info.get('active') and portal_info.get('link'):
                            return portal_info['link']
    except Exception:
        pass
    
    return None

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
    client = Client(account_config["session_name"], api_id=account_config["api_id"], api_hash=account_config["api_hash"], ipv6=False, phone_number=account_config["phone_number"])
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

async def subscribe_to_channel(username, account_data: AccountData):
    global subscribed_channels
    max_retries = 3
    base_delay = 2
    
    if not account_data.client:
        logger.error(f"[{account_data.name}] No active client available for channel subscription")
        return False
    
    for attempt in range(max_retries):
        try:
            await account_data.client.join_chat(username)
            
            account_data.subscribed_channels.add(username)
            
            if isinstance(subscribed_channels, dict):
                if username not in subscribed_channels:
                    subscribed_channels[username] = True
            else:
                if not isinstance(subscribed_channels, set):
                    subscribed_channels = set()
                subscribed_channels.add(username)
            
            logger.debug(f"[{account_data.name}] Added @{username} to subscription tracking")
            return True
            
        except Exception as e:
            error_str = str(e)
            
            if "USER_ALREADY_PARTICIPANT" in error_str:
                logger.debug(f"[{account_data.name}] Already subscribed to channel: {username}")
                
                account_data.subscribed_channels.add(username)
                
                if isinstance(subscribed_channels, dict):
                    if username not in subscribed_channels:
                        subscribed_channels[username] = True
                else:
                    if not isinstance(subscribed_channels, set):
                        subscribed_channels = set()
                    subscribed_channels.add(username)
                
                return True
            
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"[{account_data.name}] Channel subscription failed: {e}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                logger.error(f"[{account_data.name}] Channel subscription failed after all retries: {e}")
    
    return False

async def unsubscribe_from_channels(account_config, channels_set):
    if not channels_set:
        return
    
    try:
        client = Client(account_config["session_name"], api_id=account_config["api_id"], api_hash=account_config["api_hash"], ipv6=False, phone_number=account_config["phone_number"])
        async with client:
            if isinstance(channels_set, dict):
                channels_list = list(channels_set.keys())
            else:
                channels_list = list(channels_set)
            
            for username in channels_list:
                try:
                    await client.leave_chat(username)
                    logger.info(f"Unsubscribed from @{username}")
                    
                    if isinstance(channels_set, dict):
                        if username in channels_set:
                            del channels_set[username]
                    else:
                        if username in channels_set:
                            channels_set.remove(username)
                            
                except Exception as e:
                    error_str = str(e)
                    
                    if "USER_NOT_PARTICIPANT" in error_str:
                        logger.info(f"Already not subscribed to @{username}")
                        if isinstance(channels_set, dict):
                            if username in channels_set:
                                del channels_set[username]
                        else:
                            if username in channels_set:
                                channels_set.remove(username)
                    else:
                        logger.warning(f"Failed to unsubscribe from @{username}: {e}")
    except Exception as e:
        logger.error(f"Error during unsubscription process: {e}")
        return
    
    remaining_count = len(channels_set) if channels_set else 0
    if remaining_count > 0:
        logger.warning(f"Still subscribed to {remaining_count} channels")
    else:
        logger.success("Successfully unsubscribed from all channels")

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
    cookies = {
        '__ddg1_': '6PmY9RBuKWHK1TYGGlGt',
        '__ddg9_': 'USER_IP_HIDDEN',
        '__ddg8_': 'jtZrds6GidQqSrS1',
        '__ddg10_': '1758297109',
    }
    
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
    
    json_data = [
        {
            'operationName': 'me',
            'variables': {},
            'query': 'query me { me { balance starsBalance } }',
        },
    ]
    
    max_retries = 3
    base_delay = 1
    
    for attempt in range(max_retries):
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post('https://virusgift.pro/api/graphql/query', 
                                      cookies=cookies, headers=headers, json=json_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result and len(result) > 0 and 'data' in result[0] and result[0]['data'] and 'me' in result[0]['data'] and result[0]['data']['me']:
                            balance_data = result[0]['data']['me']
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

async def mark_tonnel_click(bearer_token):
    headers = {'accept': '*/*', 'authorization': bearer_token, 'content-type': 'application/json', 'origin': 'https://virusgift.pro', 'referer': 'https://virusgift.pro/roulette', 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    json_data = {'operationName': 'markTestSpinTonnelClick', 'variables': {}, 'query': 'mutation markTestSpinTonnelClick { markTestSpinTonnelClick { success __typename } }'}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    return await response.json()
        except Exception:
            pass
    return None

async def mark_portal_click(bearer_token):
    headers = {'accept': '*/*', 'authorization': bearer_token, 'content-type': 'application/json', 'origin': 'https://virusgift.pro', 'referer': 'https://virusgift.pro/roulette', 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    json_data = {'operationName': 'markPortalClick', 'variables': {}, 'query': 'mutation markPortalClick { markPortalClick { success __typename } }'}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                return await response.json()
        except Exception:
            pass
    return None

async def visit_story_link(link):
    if not link:
        return False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(link, timeout=aiohttp.ClientTimeout(total=10)) as response:
                return response.status == 200
    except Exception:
        return False

async def simulate_telegram_webapp_interaction(link, bearer_token):
    logger.info(f"Simulating WebApp interaction: {link}")
    
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            logger.debug("Opening WebApp...")
            webapp_headers = {
                'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'accept-language': 'en-US,en;q=0.5',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'none'
            }
            
            async with session.get(link, headers=webapp_headers) as response:
                await asyncio.sleep(3)
            
            logger.debug("Sending interaction signals...")
            
            interaction_mutations = [
                {
                    'operationName': 'trackWebAppInteraction',
                    'variables': {'url': link, 'action': 'open'},
                    'query': 'mutation trackWebAppInteraction($url: String!, $action: String!) { trackWebAppInteraction(url: $url, action: $action) { success } }'
                },
                {
                    'operationName': 'markPortalInteraction', 
                    'variables': {'portalUrl': link, 'interactionType': 'click'},
                    'query': 'mutation markPortalInteraction($portalUrl: String!, $interactionType: String!) { markPortalInteraction(portalUrl: $portalUrl, interactionType: $interactionType) { success } }'
                },
                {
                    'operationName': 'confirmWebAppVisit',
                    'variables': {'appUrl': link, 'duration': 5},
                    'query': 'mutation confirmWebAppVisit($appUrl: String!, $duration: Int!) { confirmWebAppVisit(appUrl: $appUrl, duration: $duration) { success } }'
                }
            ]
            
            for mutation in interaction_mutations:
                try:
                    async with session.post(graphql_url, headers=headers, json=mutation) as response:
                        if response.status == 200:
                            result = await response.json()
                            if result and 'data' in result and result['data']:
                                operation_name = mutation['operationName']
                                if operation_name in result['data']:
                                    operation_result = result['data'][operation_name]
                                    if isinstance(operation_result, dict) and operation_result.get('success'):
                                        logger.success(f"WebApp interaction successful: {operation_name}")
                                        return True
                    await asyncio.sleep(1)
                except Exception:
                    continue
            
            logger.debug("Attempting portal-specific actions...")
            await asyncio.sleep(2)
            
            portal_actions = [
                {'query': 'mutation { markPortalClick { success } }'},
                {'query': 'mutation { confirmPortalAction { success } }'},
                {'query': 'mutation { registerPortalVisit { success } }'},
                {'query': 'mutation { validatePortalInteraction { success } }'}
            ]
            
            for action in portal_actions:
                try:
                    async with session.post(graphql_url, headers=headers, json=action) as response:
                        if response.status == 200:
                            result = await response.json()
                            if result and 'data' in result:
                                for key, value in result['data'].items():
                                    if isinstance(value, dict) and value.get('success'):
                                        logger.debug(f"Portal action successful: {key}")
                                        return True
                    await asyncio.sleep(0.5)
                except Exception:
                    continue
            
            return False
            
        except Exception as e:
            logger.error(f"WebApp simulation failed: {str(e)[:100]}")
            return False

async def discover_graphql_schema(bearer_token):
    logger.debug("Discovering GraphQL schema...")
    
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    
    introspection_query = {
        'query': '''
        query IntrospectionQuery {
            __schema {
                mutationType {
                    fields {
                        name
                        description
                        args {
                            name
                            type {
                                name
                            }
                        }
                    }
                }
            }
        }
        '''
    }
    
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=introspection_query) as response:
                if response.status == 200:
                    result = await response.json()
                    if result and 'data' in result and result['data']:
                        mutations = result['data']['__schema']['mutationType']['fields']
                        portal_mutations = [m for m in mutations if 'portal' in m['name'].lower() or 'tonnel' in m['name'].lower() or 'click' in m['name'].lower()]
                        logger.debug(f"Found {len(portal_mutations)} portal-related mutations")
                        return portal_mutations
        except Exception as e:
            logger.error(f"Schema discovery failed: {str(e)[:50]}")
    
    return []

async def generate_universal_click_mutations(click_link):
    mutations = []
    
    startapp_match = re.search(r'startapp=([^&\s]+)', click_link)
    startapp_param = startapp_match.group(1) if startapp_match else None
    
    bot_match = re.search(r't\.me/([^/]+)', click_link)
    bot_name = bot_match.group(1) if bot_match else None
    
    app_match = re.search(r'/([^/]+)/dapp', click_link)
    app_name = app_match.group(1) if app_match else None
    
    base_actions = ['mark', 'confirm', 'validate', 'register', 'track', 'submit', 'execute', 'process', 'handle', 'record', 'log', 'save', 'update', 'set']
    base_objects = ['Click', 'Portal', 'Tonnel', 'Link', 'Url', 'App', 'Bot', 'Interaction', 'Visit', 'Action']
    base_contexts = ['TestSpin', 'Spin', 'Roulette', 'Game', 'WebApp', 'Telegram']
    
    mutation_names = set()
    
    for action in base_actions:
        for obj in base_objects:
            for context in base_contexts:
                mutation_names.add(f"{action}{context}{obj}")
                mutation_names.add(f"{action}{obj}{context}")
                mutation_names.add(f"{action}{obj}")
    
    if bot_name:
        for action in base_actions:
            mutation_names.add(f"{action}{bot_name.title()}Click")
            mutation_names.add(f"{action}{bot_name.title()}Visit")
    
    if app_name:
        for action in base_actions:
            mutation_names.add(f"{action}{app_name.title()}Click")
            mutation_names.add(f"{action}{app_name.title()}Visit")
    
    parameter_combinations = [
        {'link': click_link},
        {'url': click_link},
        {'telegramUrl': click_link},
        {'portalUrl': click_link},
        {'appUrl': click_link},
        {'botUrl': click_link},
        {'webAppUrl': click_link}
    ]
    
    if startapp_param:
        parameter_combinations.extend([
            {'startapp': startapp_param},
            {'startApp': startapp_param},
            {'appParam': startapp_param}
        ])
    
    if bot_name:
        parameter_combinations.extend([
            {'botName': bot_name},
            {'bot': bot_name}
        ])
    
    for mutation_name in mutation_names:
        mutations.append(f'mutation {{ {mutation_name} {{ success message __typename }} }}')
        
        for params in parameter_combinations:
            param_str = ', '.join([f'{k}: "{v}"' for k, v in params.items()])
            mutations.append(f'mutation {{ {mutation_name}({param_str}) {{ success message __typename }} }}')
            
            var_definitions = ', '.join([f'${k}: String!' for k in params.keys()])
            var_usage = ', '.join([f'{k}: ${k}' for k in params.keys()])
            mutations.append({
                'operationName': mutation_name,
                'variables': params,
                'query': f'mutation {mutation_name}({var_definitions}) {{ {mutation_name}({var_usage}) {{ success message __typename }} }}'
            })
    
    return mutations

async def handle_universal_click_requirement(bearer_token, click_link, account_data=None):
    headers = {
        'accept': '*/*',
        'authorization': bearer_token,
        'content-type': 'application/json',
        'origin': 'https://virusgift.pro',
        'referer': 'https://virusgift.pro/roulette',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    
    graphql_url = "https://virusgift.pro/api/graphql/query"
    
    logger.debug(f"Processing universal click for: {click_link}")
    
    if account_data and account_data.client and click_link.startswith('https://t.me/'):
        try:
            import re
            bot_match = re.search(r't\.me/([^/]+)', click_link)
            startapp_match = re.search(r'startapp=([^&\s]+)', click_link)
            
            if bot_match:
                bot_username = bot_match.group(1)
                startapp_param = startapp_match.group(1) if startapp_match else None
                
                logger.debug(f"Bot: {bot_username}, StartApp: {startapp_param}")
                
                try:
                    bot_peer = await account_data.client.resolve_peer(bot_username)
                    logger.debug(f"Bot resolved: {bot_peer}")
                    
                    if startapp_param:
                        start_command = f"/start {startapp_param}"
                    else:
                        start_command = "/start"
                    
                    await account_data.client.send_message(bot_username, start_command)
                    logger.debug(f"Sent command to bot: {start_command}")
                    
                    if account_data:
                        account_data.interacted_bots.add(bot_username)
                        logger.debug(f"Added {bot_username} to interacted bots list")
                    
                    await asyncio.sleep(2)
                    
                    if '/dapp' in click_link and startapp_param:
                        try:
                            web_view = await account_data.client.invoke(
                                RequestAppWebView(
                                    peer=bot_peer,
                                    app=InputBotAppShortName(bot_id=bot_peer, short_name="dapp"),
                                    platform="web",
                                    start_param=startapp_param,
                                    write_allowed=True
                                )
                            )
                            logger.debug(f"WebApp opened successfully: {web_view.url[:100]}...")
                            logger.debug("WebApp activation completed successfully!")
                            return True
                        except Exception as e:
                            logger.warning(f"WebApp opening failed: {e}")
                    
                except Exception as e:
                    logger.warning(f"Telegram bot interaction failed: {e}")
                    
        except Exception as e:
            logger.warning(f"Telegram client link opening failed: {e}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(click_link) as response:
                logger.debug(f"Direct link visit status: {response.status}")
                await asyncio.sleep(1)
    except Exception as e:
        logger.warning(f"Direct link visit failed: {e}")
    
    mutations_to_try = await generate_universal_click_mutations(click_link)
    logger.debug(f"Generated {len(mutations_to_try)} mutation variations")
    
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        successful_mutations = []
        
        for i, mutation in enumerate(mutations_to_try):
            try:
                if isinstance(mutation, dict):
                    json_data = mutation
                else:
                    json_data = {'query': mutation}
                    
                async with session.post(graphql_url, headers=headers, json=json_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        if result.get('errors'):
                            continue
                        
                        if result and 'data' in result and result['data']:
                            for key, value in result['data'].items():
                                if isinstance(value, dict) and value.get('success'):
                                    logger.success(f"Successful mutation {i+1}: {key} -> {value}")
                                    successful_mutations.append(key)
                        
                if i % 10 == 0:
                    await asyncio.sleep(0.1)
                    
            except Exception:
                continue
        
        if successful_mutations:
            logger.debug(f"Found {len(successful_mutations)} working mutations: {successful_mutations[:5]}")
            return True
    
    logger.warning("No working mutations found")
    return False

async def auto_visit_telegram_link(link, bearer_token, account_data=None):
    if not link or 't.me' not in link:
        return False
    
    logger.debug("Starting automated portal bypass...")
    

    success = await simulate_telegram_webapp_interaction(link, bearer_token)
    if success:
        return True
    

    portal_mutations = await discover_graphql_schema(bearer_token)
    

    if portal_mutations:
        logger.debug("Trying discovered mutations...")
        headers = {
            'accept': '*/*',
            'authorization': bearer_token,
            'content-type': 'application/json',
            'origin': 'https://virusgift.pro',
            'referer': 'https://virusgift.pro/roulette',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        
        successful_mutations = []
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            for mutation in portal_mutations:
                mutation_name = mutation['name']
                try:
                    query = f'mutation {{ {mutation_name} {{ success }} }}'
                    async with session.post(graphql_url, headers=headers, json={'query': query}) as response:
                        if response.status == 200:
                            result = await response.json()
                            if result and 'data' in result and result['data']:
                                if mutation_name in result['data'] and result['data'][mutation_name].get('success'):
                                    logger.debug(f"Schema-based success: {mutation_name}")
                                    successful_mutations.append(mutation_name)
                    await asyncio.sleep(0.3)
                except Exception:
                    continue
            
        
            if successful_mutations:
                logger.info("Executing all portal/tonnel mutations...")
                portal_tonnel_mutations = ['markTestSpinPortalClick', 'markTestSpinTonnelClick', 'markPortalClick', 'markTonnelClick']
                
                for mutation_name in portal_tonnel_mutations:
                    try:
                        query = f'mutation {{ {mutation_name} {{ success }} }}'
                        async with session.post(graphql_url, headers=headers, json={'query': query}) as response:
                            if response.status == 200:
                                result = await response.json()
                                if result and 'data' in result and result['data']:
                                    if mutation_name in result['data'] and result['data'][mutation_name].get('success'):
                                        logger.debug(f"Additional success: {mutation_name}")
                        await asyncio.sleep(0.5)
                    except Exception:
                        continue
                
                return True
    

    success = await handle_universal_click_requirement(bearer_token, link, account_data)
    if success:
        return True
    
    logger.warning("All automated methods exhausted")
    return False

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
    headers = {'accept': '*/*', 'authorization': bearer_token, 'content-type': 'application/json', 'origin': 'https://virusgift.pro', 'referer': 'https://virusgift.pro/roulette', 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    json_data = {'operationName': 'claimRoulettePrize', 'variables': {'input': {'userPrizeId': user_prize_id}}, 'query': 'mutation claimRoulettePrize($input: ClaimRoulettePrizeInput!) { claimRoulettePrize(input: $input) { success message telegramGift __typename } }'}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        try:
            async with session.post(graphql_url, headers=headers, json=json_data) as response:
                if response.status == 200:
                    return await response.json()
        except Exception:
            pass
    return None

async def check_and_claim_rewards(bearer_token, account_data=None):
    inventory_result = await get_inventory_prizes(bearer_token)
    
    if not inventory_result:
        return False
        
    if 'errors' in inventory_result:
        for error in inventory_result.get('errors', []):
            if error.get('extensions', {}).get('code') == 'UNAUTHORIZED':
                return None
        return False
    
    if not inventory_result.get('data', {}).get('getRouletteInventory', {}).get('success'):
        return False
    
    prizes = inventory_result.get('data', {}).get('getRouletteInventory', {}).get('prizes', [])
    rewards_found = False
    
    for prize_data in prizes or []:
        prize = prize_data.get('prize', {})
        prize_name = prize.get('name', '')
        user_prize_id = prize_data.get('userRoulettePrizeId')
        is_claimable = prize.get('isClaimable', False)
        
        if ('Stars' in prize_name or 'Virus' in prize_name) and is_claimable and user_prize_id:
            
            await mark_tonnel_click(bearer_token)
            await mark_portal_click(bearer_token)
            
            claim_result = await claim_prize(bearer_token, user_prize_id)
            
            if claim_result and 'data' in claim_result and claim_result['data'] and 'claimRoulettePrize' in claim_result['data']:
                if claim_result['data']['claimRoulettePrize'].get('success'):
                    rewards_found = True
                    
                    await cleanup_after_reward(account_data)
                    
                else:
                    pass
            elif claim_result and 'errors' in claim_result:
                error_msg = claim_result['errors'][0].get('message', 'Unknown error')
                if 'portal' in error_msg.lower() or 'tonnel' in error_msg.lower():
                     telegram_link = await get_portal_link(bearer_token)
                     if 'extensions' in claim_result['errors'][0] and 'link' in claim_result['errors'][0]['extensions']:
                         telegram_link = claim_result['errors'][0]['extensions']['link']
                     
                     if not telegram_link:
                         continue
                     
                     auto_success = await auto_visit_telegram_link(telegram_link, bearer_token, account_data)
                     
                     if auto_success:
                         await asyncio.sleep(1)
                         retry_claim_result = await claim_prize(bearer_token, user_prize_id)
                         
                         if retry_claim_result and 'data' in retry_claim_result and retry_claim_result['data'] and 'claimRoulettePrize' in retry_claim_result['data']:
                             if retry_claim_result['data']['claimRoulettePrize'].get('success'):
                                 rewards_found = True
                                 
                                 await cleanup_after_reward(account_data)
                                 
                                 continue
                else:
                    pass
            await asyncio.sleep(0.5)
    
    return rewards_found

async def cleanup_after_reward(account_data: AccountData):
    if not account_data or not account_data.client:
        return
    
    if account_data.subscribed_channels:
        for channel in list(account_data.subscribed_channels):
            try:
                if isinstance(channel, str):
                    if channel.startswith('http') or channel.startswith('https'):
                        if 't.me/' in channel:
                            url_parts = channel.split('t.me/')
                            if len(url_parts) > 1:
                                username_part = url_parts[1]
                                if username_part.startswith('+') or username_part.startswith('joinchat/'):
                                    await account_data.client.leave_chat(channel)
                                    logger.success(f"Unsubscribed from channel invite link: {channel}")
                                else:
                                    username = username_part.split('/')[0].split('?')[0]
                                    await account_data.client.leave_chat(username)
                            else:
                                await account_data.client.leave_chat(channel)
                        else:
                            await account_data.client.leave_chat(channel)
                    elif channel.startswith('@'):
                        username = channel[1:]  
                        await account_data.client.leave_chat(username)
                        logger.success(f"Unsubscribed from channel: @{username}")
                    elif channel.startswith('+') or channel.startswith('joinchat/'):
                        await account_data.client.leave_chat(channel)
                        logger.success(f"Unsubscribed from invite link: {channel}")
                    else:
                        await account_data.client.leave_chat(channel)
                        logger.success(f"Unsubscribed from channel: {channel}")
                else:
                    await account_data.client.leave_chat(channel)
                    logger.success(f"Unsubscribed from channel: {channel}")
                
                account_data.subscribed_channels.remove(channel)
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"Failed to unsubscribe from {channel}: {e}")
    
    if account_data.interacted_bots:
        logger.info(f"Deleting {len(account_data.interacted_bots)} bot chats...")
        for bot_username in list(account_data.interacted_bots):
            try:
                await account_data.client.delete_chat_history(bot_username, revoke=True)
                logger.success(f"Deleted chat history with bot: {bot_username}")
                
                account_data.interacted_bots.remove(bot_username)
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"Failed to delete chat with {bot_username}: {e}")

async def refresh_bearer_token(account_config, account_data=None):
    """ --- BEARER TOKEN REFRESH WITH IMPROVED CLIENT HANDLING --- """
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            """ --- CREATE NEW CLIENT INSTANCE FOR TOKEN REFRESH --- """
            temp_client = Client(
                f"{account_config['session_name']}_temp_{attempt}",
                account_config["api_id"],
                account_config["api_hash"],
                phone_number=account_config["phone_number"],
                in_memory=True
            )
            
            async with temp_client:
                """ --- GET FRESH INIT DATA --- """
                bot_entity = await temp_client.get_users('virus_play_bot')
                bot = InputUser(user_id=bot_entity.id, access_hash=bot_entity.raw.access_hash)
                peer = await temp_client.resolve_peer('virus_play_bot')
                bot_app = InputBotAppShortName(bot_id=bot, short_name="app")
                web_view = await temp_client.invoke(RequestAppWebView(peer=peer, app=bot_app, platform="android"))
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
                
                """ --- GET NEW BEARER TOKEN --- """
                bearer_token = await get_bearer_token(init_data)
                if not bearer_token:
                    logger.warning(f"Attempt {attempt + 1}/{max_retries}: Failed to get bearer_token")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(3)
                        continue
                    return None
                
                """ --- VALIDATE NEW TOKEN BEFORE RETURNING --- """
                is_valid = await validate_bearer_token(bearer_token)
                if is_valid:
                    logger.success(f"Bearer token refreshed successfully on attempt {attempt + 1}")
                    return bearer_token
                else:
                    logger.warning(f"Attempt {attempt + 1}/{max_retries}: New token validation failed")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(5)
                        continue
                    
        except Exception as e:
            logger.error(f"Attempt {attempt + 1}/{max_retries}: Token refresh error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
                continue
    
    logger.error("Failed to refresh bearer token after all attempts")
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
    
    for account_name, account_data in account_manager.accounts.items():
        balance = account_data.balance if account_data.balance else 0
        formatted_balance = format_number_with_spaces(balance)
        username = account_data.username if account_data.username else account_name
        
        next_time = account_data.next_roulette_time
        if next_time and next_time != "Unknown" and next_time != "⏳ Unknown...":
            try:
                dt = datetime.fromisoformat(next_time.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                diff = dt - now
                
                if diff.total_seconds() <= 0:
                    time_display = "Ready!"
                else:
                    hours = int(diff.total_seconds() // 3600)
                    minutes = int((diff.total_seconds() % 3600) // 60)
                    
                    if hours > 0:
                        time_display = f"{hours}h {minutes}m"
                    else:
                        time_display = f"{minutes}m"
            except (ValueError, TypeError):
                time_display = "⏳ unknown..."
        else:
            time_display = "⏳ unknown..."
        
        text += f"`@{username}` \- ⭐️*{formatted_balance}* \- 🕐 `{time_display}`\n"
    
    return text

async def get_main_menu_keyboard() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()
    return builder.as_markup()


async def setup_bot_handlers():
    
    @dp.message(Command("start"))
    async def start_command(message: types.Message):
        if message.from_user.id != admin_id:
            await message.answer("❌ Access denied")
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
        await message.answer(text, reply_markup=keyboard, parse_mode="MarkdownV2")
        
async def update_single_account_status(account_name, account_data):
    """Update a single account's status asynchronously"""
    try:
        balance_data = await get_account_balance(account_data.bearer_token)
        account_data.balance = balance_data.get('stars_balance', 0) if isinstance(balance_data, dict) else 0
        
        next_time = await get_next_free_spin_time(account_data.bearer_token)
        account_data.next_roulette_time = next_time
    except Exception as e:
        logger.error(f"Failed to update {account_name}: {e}")


account_manager = AccountManager()

async def update_all_accounts_status():
    for account_name, account_data in account_manager.accounts.items():
        if account_data.bearer_token:
            try:
        
                balance_data = await get_account_balance(account_data.bearer_token)
                account_data.balance = balance_data.get('stars_balance', 0) if isinstance(balance_data, dict) else 0
                
        
                next_time = await get_next_free_spin_time(account_data.bearer_token)
                account_data.next_roulette_time = next_time
                
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
        
        result = await start_roulette_spin(account_data.bearer_token)
        
        if result and 'errors' in result:
            for error in result['errors']:
                error_code = error.get('extensions', {}).get('code', 'UNKNOWN')
                error_message = error.get('message', 'Unknown error')
                if error_code != 'TELEGRAM_SUBSCRIPTION_REQUIRED':
                    logger.error(f"Roulette API error [{error_code}]: {error_message}")
                else:
                    pass
                
                """ --- INSUFFICIENT BALANCE ERROR --- """
                if error.get('extensions', {}).get('code') == 'INSUFFICIENT_BALANCE':
                    logger.error(f"[{account_name}] Roulette spin failed - insufficient balance")
                    """ --- UPDATE NEXT ROULETTE TIME TO PREVENT RETRY LOOP --- """
                    next_time = await get_next_free_spin_time(account_data.bearer_token)
                    if next_time:
                        account_data.next_roulette_time = next_time
                        logger.info(f"[{account_name}] Updated next roulette time to: {next_time}")
                    else:
                        """ --- SET TO 24 HOURS FROM NOW IF API CALL FAILS --- """
                        from datetime import timedelta
                        next_time = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace('+00:00', 'Z')
                        account_data.next_roulette_time = next_time
                        logger.info(f"[{account_name}] Set next roulette time to 24h from now: {next_time}")
                    return False
                
                elif error.get('extensions', {}).get('code') == 'TEST_SPIN_URL_CLICK_REQUIRED':
                    click_link = error.get('extensions', {}).get('link')
                    if click_link:
                        click_link = click_link.strip().strip('`').strip()
                        logger.info(f"[{account_name}] URL click required: {click_link}")
                        
                        logger.info(f"[{account_name}] Attempting specialized test spin click...")
                        click_success = await handle_universal_click_requirement(account_data.bearer_token, click_link, account_data)
                        
                        if click_success:
                            logger.debug(f"[{account_name}] Test spin click successful")
                            logger.info(f"[{account_name}] Waiting 5 seconds for WebApp activation to complete...")
                            await asyncio.sleep(5)
                            result = await start_roulette_spin(account_data.bearer_token)
                        else:
                            logger.error(f"[{account_name}] All click methods failed")
                            return False
        
        if result and 'errors' in result:
            for error in result['errors']:
                if error.get('extensions', {}).get('code') == 'TELEGRAM_SUBSCRIPTION_REQUIRED':
                    username = error.get('extensions', {}).get('username')
                    url = error.get('extensions', {}).get('url')
                    if url:
                         subscription_success = await subscribe_to_channel(url, account_data)
                         if subscription_success:
                             logger.success(f"[{account_name}] Successfully subscribed to channel")
             
                             await asyncio.sleep(2)
                             result = await start_roulette_spin(account_data.bearer_token)
                         else:
                             logger.error(f"[{account_name}] Failed to subscribe to channel")
                    elif username:
                         subscription_success = await subscribe_to_channel(username, account_data)
                         if subscription_success:
                             logger.success(f"[{account_name}] Successfully subscribed to @{username}")
             
                             await asyncio.sleep(2)
                             result = await start_roulette_spin(account_data.bearer_token)
                         else:
                             logger.error(f"[{account_name}] Failed to subscribe to @{username}")
        
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
                
                """ --- PRIZE PROCESSING SECTION --- """
                prize_info = "Unknown prize"
                user_prize_id = spin_data.get('userPrizeId')
                is_stars_prize = False
                success_message = ""
                
                if 'prize' in spin_data and spin_data['prize']:
                    prize_data = spin_data['prize']
                    if 'name' in prize_data:
                        prize_info = prize_data['name']
                        if isinstance(prize_info, str) and ('stars' in prize_info.lower() or 'virus' in prize_info.lower()):
                            is_stars_prize = True
                        elif isinstance(prize_info, (int, float)):
                            prize_info = str(prize_info)
                    elif 'caption' in prize_data:
                        prize_info = prize_data['caption']
                        if isinstance(prize_info, str) and ('stars' in prize_info.lower() or 'virus' in prize_info.lower()):
                            is_stars_prize = True
                        elif isinstance(prize_info, (int, float)):
                            prize_info = str(prize_info)
                
                if is_stars_prize and user_prize_id:
                    claim_result = await claim_prize(account_data.bearer_token, user_prize_id)
                    if claim_result and claim_result.get('data', {}).get('claimRoulettePrize', {}).get('success', False):
                        balance_result = await get_account_balance(account_data.bearer_token)
                        virus_balance = balance_result.get('virus_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                        stars_balance = balance_result.get('stars_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                        
                        formatted_virus_balance = format_number_with_spaces(virus_balance)
                        formatted_stars_balance = format_number_with_spaces(stars_balance)
                        
                        success_message = f"> @{account_data.username} successfully spun and claimed the roulette\n"
                        success_message += f"⭐ Prize: {prize_info}\n"
                        success_message += f"💰 Stars Balance: {formatted_stars_balance}\n"
                        success_message += f"💰 Virus Balance: {formatted_virus_balance}"
                        
                        logger.success(f"[{account_name}] Prize claimed: {prize_info} | Stars: {stars_balance} | Virus: {virus_balance}")
                    else:
                        balance_result = await get_account_balance(account_data.bearer_token)
                        virus_balance = balance_result.get('virus_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                        stars_balance = balance_result.get('stars_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                        
                        formatted_virus_balance = format_number_with_spaces(virus_balance)
                        formatted_stars_balance = format_number_with_spaces(stars_balance)
                        
                        success_message = f"> @{account_data.username} successfully spun the roulette\n"
                        success_message += f"⭐ Prize: {prize_info} (failed to claim)\n"
                        success_message += f"💰 Stars Balance: {formatted_stars_balance}\n"
                        success_message += f"💰 Virus Balance: {formatted_virus_balance}"
                else:
                    balance_result = await get_account_balance(account_data.bearer_token)
                    virus_balance = balance_result.get('virus_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                    stars_balance = balance_result.get('stars_balance', 'Unknown') if isinstance(balance_result, dict) else 'Unknown'
                    
                    formatted_virus_balance = format_number_with_spaces(virus_balance)
                    formatted_stars_balance = format_number_with_spaces(stars_balance)
                    
                    success_message = f"> @{account_data.username} received an unknown prize\n"
                    success_message += f"❓ Unknown Prize: {prize_info}\n"
                    success_message += f"💰 Stars Balance: {formatted_stars_balance}\n"
                    success_message += f"💰 Virus Balance: {formatted_virus_balance}"
                
                """ --- NOTIFICATION SENDING SECTION --- """
                if success_message:
                    logger.info(f"[{account_name}] Sending success notification to Telegram")
                    notification_result = await send_notification(success_message)
                    if notification_result:
                        logger.success(f"[{account_name}] Success notification sent successfully")
                    else:
                        logger.warning(f"[{account_name}] Failed to send success notification")
                else:
                    logger.warning(f"[{account_name}] No success message generated - notification not sent")
                
                if hasattr(account_data, 'subscribed_channels') and account_data.subscribed_channels:
                    try:
                        await cleanup_after_reward(account_data)
                    except Exception as e:
                        logger.error(f"[{account_name}] Error during cleanup: {e}")
                
                try:
                    balance_result = await get_account_balance(account_data.bearer_token)
                    account_data.balance = balance_result.get('stars_balance', 0) if isinstance(balance_result, dict) else 0
                    logger.info(f"[{account_name}] Updated account balance - Stars: {account_data.balance}")
                except Exception as e:
                    logger.error(f"[{account_name}] Failed to update account balance: {e}")
                
                await update_all_accounts_status()
            else:
                logger.warning(f"[{account_name}] Roulette spin failed or not ready yet")
                return False
            
            if 'subscriptions' in result:
                for subscription in result['subscriptions']:
                    channel_username = subscription.get('username')
                    if channel_username and channel_username not in account_data.subscribed_channels:
                        success = await subscribe_to_channel(channel_username, account_data)
                        if success:
                            logger.debug(f"[{account_name}] Subscribed to channel: {channel_username}")
            
            return True
        else:
            """ --- ROULETTE SPIN FAILURE SECTION --- """
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

def format_number_with_spaces(number):
    """Format number with spaces every 3 digits"""
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
    

    """ --- SEQUENTIAL SESSION CREATION --- """
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
    
    async def roulette_worker():
        while True:
            try:
                for account_name, account_data in account_manager.accounts.items():
                    if account_data.bearer_token and account_data.next_roulette_time:
                        try:
                
                            dt = datetime.fromisoformat(account_data.next_roulette_time.replace('Z', '+00:00'))
                            now = datetime.now(timezone.utc)
                            
                            if now >= dt:
                                success = await process_account_roulette(account_name, account_data)
                                if success:
                                    next_time = await get_next_free_spin_time(account_data.bearer_token)
                                    account_data.next_roulette_time = next_time
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
    """ --- INITIALIZE ACCOUNT CLIENT WITH CREDENTIAL VALIDATION --- """
    try:
        api_id = config.get("api_id")
        api_hash = config.get("api_hash")
        session_name = config.get("session_name")

        if not session_name:
            logger.error(f"[{account_name}] Missing session_name in configuration")
            return False

        session_file = f"{session_name}.session"

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
            # Remove unstarted account to skip downstream steps
            try:
                del account_manager.accounts[account_name]
            except Exception:
                pass
            return False
    except Exception as e:
        logger.error(f"[{account_name}] Unexpected error during initialization: {e}")
        return False

async def get_account_token_and_username(account_name, config, account_manager):
    """Get bearer token and username for an account"""
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
                """ --- GRACEFUL SHUTDOWN --- """
                try:
                    # Stop all Pyrogram clients
                    for account_name, account_data in account_manager.accounts.items():
                        try:
                            if account_data.client:
                                await account_data.client.stop()
                                logger.info(f"[{account_name}] Client stopped")
                        except Exception:
                            pass
                    
                    # Close Aiogram bot session
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
