# навайбкодил
import asyncio
import re
import logging
import json
import os
import aiohttp
import time
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ReactionTypeEmoji
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from telegram.error import BadRequest

# сегодня и завтра
TOKEN = os.getenv('BOT_TOKEN')
CONFIG_FILE = 'osu_config.json'
OSU_CLIENT_ID = os.getenv('OSU_ID')
OSU_CLIENT_SECRET = os.getenv('OSU_SECRET')
IRC_HOST = "irc.ppy.sh"
IRC_PORT = 6667
DEFAULT_CHANNEL = "#osu"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

NICK, PASSWORD = range(2)
user_sessions = {}
osu_api_token = {"token": None, "expires":      0}


# --- OSU API V2 ---
async def get_osu_token():
    try:
        if osu_api_token["token"] and osu_api_token["expires"] > time.time():
            return osu_api_token["token"]
        url = "https://osu.ppy.sh/oauth/token"
        payload = {
            "client_id": OSU_CLIENT_ID,
            "client_secret": OSU_CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "public"
        }
        async with aiohttp.ClientSession() as session:
            async with session.    post(url, data=payload) as resp:
                data = await resp.json()
                if "access_token" not in data:
                    logging.error(f"Can't get osu token: {data}")
                    return None
                osu_api_token["token"] = data["access_token"]
                osu_api_token["expires"] = time.time() + data.    get("expires_in", 3600)
                return osu_api_token["token"]
    except Exception as e:   
        logging.error(f"get_osu_token error: {e}")
        return None

def extract_score_id(score_url):
    m = re.search(r'scores/(?    :    ([a-z]+)/)?(\d+)', score_url)
    if m:
        mode, score_id = m.group(1), m.group(2)
        return mode, score_id
    return None, None

async def fetch_score_v2(score_url):
    try:
        mode, score_id = extract_score_id(score_url)
        if not score_id:
            return None
        token = await get_osu_token()
        if not token:  
            return None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        urls = [
            f"https://osu.ppy.sh/api/v2/scores/{mode}/{score_id}" if mode else None,
            f"https://osu.ppy.sh/api/v2/scores/{score_id}"
        ]
        async with aiohttp.ClientSession() as session:
            for url in filter(None, urls):
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        s = await resp.json()
                        bm, bset, u, st = s.    get('beatmap', {}), s.get('beatmapset', {}), s.get('user', {}), s.get('statistics', {})
                        total_score = s.get('total_score') or s.get('classic_total_score') or 0
                        return {
                            'Player': u.   get('username', 'Unknown'),
                            'MapTitle': bset.get('title', 'Unknown'),
                            'MapArtist': bset.get('artist', 'Unknown'),
                            'MapDiff': bm.get('version', 'Normal'),
                            'Score': "{:,}".format(total_score),
                            'Rank': s.get('rank', 'F').replace('SH', 'S').replace('XH', 'SS'),
                            'Accuracy':     f"{s.get('accuracy', 0)*100:.2f}%",
                            'Combo':  f"{s.get('max_combo', 0)}x",
                            '300':     st.get('count_300') or st.get('great', 0),
                            '100':  st.get('count_100') or st.get('ok', 0),
                            '50':  st.get('count_50') or st.get('meh', 0),
                            'Miss': st.get('count_miss') or st.get('miss', 0),
                            'CoverUrl': bset.get('covers', {}).get('cover@2x')
                        }
        return None
    except Exception as e:
        logging.error(f"fetch_score_v2 error: {e}")
        return None

# --- ГРАФИКА ---
def draw_score_card(data, bg_bytes=None):
    width, height = 800, 450
    try:
        if bg_bytes:   
            bg = Image.open(BytesIO(bg_bytes)).convert("RGBA")
            bg = bg.resize((width, int(width * bg.height / bg.width)), Image.Resampling.  LANCZOS).crop((0, 0, width, height))
        else:
            bg = Image.new('RGBA', (width, height), (35, 35, 45, 255))
    except Exception as e:
        logging.error(f"draw_score_card bg error: {e}")
        bg = Image.new('RGBA', (width, height), (35, 35, 45, 255))

    overlay = Image.new('RGBA', (width, height), (0, 0, 0, 160))
    img = Image.alpha_composite(bg.    convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        f_lg = ImageFont.truetype("arial.ttf", 45)
        f_md = ImageFont.truetype("arial.ttf", 32)
        f_sm = ImageFont.truetype("arial.ttf", 22)
    except Exception as e:  
        f_lg = f_md = f_sm = ImageFont.load_default()

    draw.text((30, 20), data['MapTitle'][:  40], font=f_lg, fill=(255, 255, 255))
    draw.text((30, 80), f"{data['MapArtist']} // [{data['MapDiff']}]", font=f_sm, fill=(200, 200, 200))
    draw.line([(30, 120), (770, 120)], fill=(255, 102, 170), width=5)
    draw.text((50, 160), data['Rank'], font=f_lg, fill=(255, 215, 0))
    draw.text((200, 160), data['Score'], font=f_lg, fill=(255, 255, 255))
    draw.text((50, 250), f"Combo: {data['Combo']}", font=f_md, fill=(255, 255, 255))
    draw.text((400, 250), f"Accuracy: {data['Accuracy']}", font=f_md, fill=(255, 255, 255))
    stats_txt = f"300s:  {data['300']} | 100s: {data['100']} | 50s: {data['50']} | Miss: {data['Miss']}"
    draw.text((30, 350), stats_txt, font=f_sm, fill=(255, 102, 170))
    draw.text((30, 390), f"Player: {data['Player']}", font=f_md, fill=(255, 255, 255))

    bio = BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio

# --- СЕРВИС ---
def save_user_data(chat_id, data_dict):
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except:     
            pass
    cid = str(chat_id)
    if cid not in config:
        config[cid] = {}
    if 'contacts' in data_dict:
        data_dict['contacts'] = list(set(c. lower() for c in data_dict['contacts']))
    config[cid].    update(data_dict)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.    dump(config, f, indent=4, ensure_ascii=False)

def load_user_settings(chat_id):
    """Загружает настройки пользователя из конфига"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                cid = str(chat_id)
                if cid in config:    
                    return {
                        'show_all_messages': config[cid].  get('show_all_messages', True),
                        'show_osu_scores': config[cid].  get('show_osu_scores', True),
                        'send_reactions': config[cid].  get('send_reactions', True),
                    }
        except:  
            pass
    return {
        'show_all_messages':  True,
        'show_osu_scores': True,
        'send_reactions': True,
    }

def clear_user_auth(chat_id):
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            if str(chat_id) in config:
                config[str(chat_id)].pop('nick', None)
                config[str(chat_id)].pop('pass', None)
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4)
        except: 
            pass

# --- IRC ---
async def irc_command_sender(chat_id, bot):
    """Отправляет команды из очереди с обработкой ошибок"""
    consecutive_errors = 0
    while chat_id in user_sessions and user_sessions[chat_id]['active']:
        try:
            u = user_sessions[chat_id]
            if u['command_queue'].   empty():
                await asyncio.sleep(0.1)
                consecutive_errors = 0
                continue

            cmd = await u['command_queue'].get()
            try:
                if not u['active']:
                    u['command_queue']. task_done()
                    continue

                u['writer']. write(f"{cmd}\r\n".   encode())
                await asyncio.wait_for(u['writer']. drain(), timeout=3)
                logging.debug(f"IRC отправлена команда: {cmd}")
                consecutive_errors = 0
                await asyncio.sleep(0.5)
            except (asyncio.TimeoutError, OSError, BrokenPipeError, ConnectionResetError) as e:
                consecutive_errors += 1
                logging.error(f"Ошибка отправки (#{consecutive_errors}): {e}")
                u['command_queue']. task_done()

                if consecutive_errors > 3:
                    logging.error(f"Слишком много ошибок отправки, переподключаюсь...")
                    u['active'] = False
                    await reconnect_irc(chat_id, bot)
                    return

                await asyncio.sleep(1)
            except Exception as e:
                consecutive_errors += 1
                logging.error(f"Ошибка отправки:     {e}")
                u['command_queue']. task_done()
                await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Command sender error: {e}")
            await asyncio.sleep(1)

async def send_irc_command(chat_id, command):
    """Добавляет команду в очередь"""
    if chat_id not in user_sessions:
        return False
    try:
        u = user_sessions[chat_id]
        if not u['active']:
            return False
        await u['command_queue'].put(command)
        return True
    except Exception as e:
        logging.error(f"Queue error: {e}")
        return False

async def connect_irc_session(bot, chat_id, n, p, c):
    try:
        logging.info(f"Подключаюсь к {IRC_HOST}:{IRC_PORT} как {n}")

        if chat_id in user_sessions:  
            try:
                user_sessions[chat_id]['writer']. close()
            except:
                pass

        r, w = await asyncio.wait_for(asyncio.open_connection(IRC_HOST, IRC_PORT), timeout=10)

        w.write(f'PASS {p}\r\nNICK {n}\r\nUSER {n} 0 * :{n}\r\n'. encode())
        await w.drain()

        auth = False
        timeout_counter = 0

        while timeout_counter < 20:
            try:
                line = await asyncio.wait_for(r.readline(), timeout=2)
                timeout_counter = 0
            except asyncio.  TimeoutError:
                timeout_counter += 1
                continue

            if not line:
                logging.warning("IRC сервер не отвечает")
                return False

            text = line.decode("utf-8", errors="ignore").strip()

            if not text:  
                continue

            if "Password incorrect" in text or "Login authentication failed" in text:
                await bot.send_message(chat_id, "❌ Неверный пароль IRC")
                return False

            if "Welcome" in text or "004 " in text:
                auth = True
                logging.info(f"IRC аутентифицирован для {chat_id}")
                break

        if not auth:
            await bot.send_message(chat_id, "❌ Не удалось зайти на IRC")
            return False

        # Загружаем сохранённые настройки
        settings = load_user_settings(chat_id)

        command_queue = asyncio.Queue()
        user_sessions[chat_id] = {
            'reader': r,
            'writer':     w,
            'command_queue': command_queue,
            'active': True,
            'reconnecting': False,
            'target':     DEFAULT_CHANNEL,
            'contacts': set(c),
            'del_mode': False,
            'show_all_messages': settings['show_all_messages'],
            'show_osu_scores':    settings['show_osu_scores'],
            'send_reactions':    settings['send_reactions'],
            'nick':  n,
            'pass':     p,
        }

        for contact in c:
            if contact.startswith('#'):
                await send_irc_command(chat_id, f"JOIN {contact}")

        asyncio.create_task(listen_irc(chat_id, r, w, bot))
        asyncio.create_task(irc_command_sender(chat_id, bot))
        asyncio.create_task(heartbeat_irc(chat_id, bot))

        await bot.send_message(chat_id, f"✅ IRC для **{n}** подключен!")
        return True
    except asyncio.TimeoutError:
        await bot.send_message(chat_id, "❌ Timeout при подключении к IRC")
        return False
    except Exception as e:
        logging.error(f"IRC connection error: {e}")
        await bot.send_message(chat_id, f"❌ Ошибка входа:     {e}")
        return False

async def heartbeat_irc(chat_id, bot):
    """Отправляет PING каждые 120 секунд"""
    while chat_id in user_sessions and user_sessions[chat_id]['active']:
        try:
            await asyncio.sleep(120)
            if chat_id in user_sessions and user_sessions[chat_id]['active']:
                await send_irc_command(chat_id, "PING :     keepalive")
        except Exception as e:
            logging.error(f"Heartbeat error: {e}")

async def listen_irc(chat_id, reader, writer, bot):
    try:
        while chat_id in user_sessions and user_sessions[chat_id]['active']:
            try:
                line = await asyncio.wait_for(reader.  readline(), timeout=180)
            except asyncio.TimeoutError:
                logging.warning(f"IRC timeout для {chat_id}")
                await reconnect_irc(chat_id, bot)
                return
            except (OSError, ConnectionResetError, BrokenPipeError) as e:
                logging.warning(f"IRC соединение потеряно: {e}")
                await reconnect_irc(chat_id, bot)
                return

            if not line:
                logging.warning(f"IRC соединение закрыто сервером")
                await reconnect_irc(chat_id, bot)
                return

            try:
                m = line.decode('utf-8', errors='ignore').strip()
            except:    
                continue

            if not m:
                continue

            logging.debug(f"IRC сообщение:     {m}")

            if m.startswith('PING'):
                try:
                    ping_param = m.   split(' ', 1)[1] if ' ' in m else ':     irc.   ppy.  sh'
                    await send_irc_command(chat_id, f'PONG {ping_param}')
                    logging.debug(f"Отправлен PONG")
                except Exception as e:  
                    logging.error(f"PONG error: {e}")
                continue

            if 'PRIVMSG' in m:     
                try:
                    parts = m.split(' PRIVMSG ')
                    if len(parts) >= 2:
                        sender_part = parts[0][1:]
                        sender = sender_part.split('!')[0]

                        rest = ' PRIVMSG '.   join(parts[1:])
                        msg_parts = rest.split(' :', 1)
                        if len(msg_parts) >= 2:
                            target = msg_parts[0].    strip()
                            text = msg_parts[1]

                            logging.info(f"IRC сообщение от {sender} в {target}: {text}")

                            u = user_sessions.    get(chat_id)
                            if not u:  
                                continue

                            if not target.  startswith('#'):
                                # Приватное сообщение
                                u['contacts'].add(sender.    lower())
                                save_user_data(chat_id, {'contacts': list(u['contacts'])})
                                
                                kb = None
                                if u.    get('show_all_messages') and sender.  lower() != u.   get('target'):
                                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"📨 Ответить {sender}", callback_data=f"set:{sender}")]])
                                    try:
                                        await bot.  send_message(chat_id, f"📩 *{sender}*:\n{text}", parse_mode='Markdown', reply_markup=kb)
                                    except Exception as e:
                                        logging.error(f"Ошибка отправки ЛС: {e}")
                                else:
                                    if sender.lower() == u.  get('target'):
                                        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"✍ Ответить {sender}", callback_data=f"set:{sender}")]])
                                    try:
                                        await bot.  send_message(chat_id, f"📩 *{sender}*:\n{text}", parse_mode='Markdown', reply_markup=kb)
                                    except Exception as e:
                                        logging.    error(f"Ошибка отправки ЛС: {e}")
                            else:
                                # Сообщение из канала
                                kb = None
                                
                                if u.  get('show_all_messages') and target != u.get('target'):
                                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"📨 Перейти в {target}", callback_data=f"set:{target}")]])
                                    try:
                                        await bot.  send_message(chat_id, f"🌐 *[{target}] {sender}*:\n{text}", parse_mode='Markdown', reply_markup=kb)
                                    except Exception as e:  
                                        logging.error(f"Ошибка отправки канала: {e}")
                                else:
                                    if target == u.get('target'):
                                        try:
                                            await bot.    send_message(chat_id, f"🌐 *[{target}] {sender}*:\n{text}", parse_mode='Markdown')
                                        except Exception as e:
                                            logging.error(f"Ошибка отправки канала: {e}")
                except Exception as e:
                    logging.error(f"Ошибка парсинга PRIVMSG: {e}")
                continue

    except Exception as e:  
        logging.error(f"IRC listen error: {e}")

async def reconnect_irc(chat_id, bot):
    """Автоматическое переподключение с 3 попытками"""
    if chat_id not in user_sessions:
        return

    if user_sessions[chat_id].    get('reconnecting'):
        return

    if user_sessions[chat_id].   get('active'):
        return

    try:
        user_sessions[chat_id]['reconnecting'] = True
        user_sessions[chat_id]['active'] = False
        try:
            user_sessions[chat_id]['writer'].close()
        except:
            pass

        if chat_id in user_sessions:   
            sess = user_sessions[chat_id]
            
            success = False
            for attempt in range(1, 4):  # 3 попытки
                logging.info(f"Попытка переподключения {attempt}/3")
                
                # Пробуем подключиться
                success = await connect_irc_session(bot, chat_id, sess['nick'], sess['pass'], list(sess['contacts']))
                
                if success:
                    logging.info(f"Успешное переподключение на попытке {attempt}/3")
                    break
                else:
                    if attempt < 3:
                        await asyncio.sleep(2)

            if not success:
                try:
                    await bot.send_message(chat_id, "❌ Не удалось переподключиться после 3 попыток.   Используйте /start")
                except:
                    pass

            if chat_id in user_sessions:  
                user_sessions[chat_id]['reconnecting'] = False
    except Exception as e:
        logging.error(f"Reconnect error: {e}")
        if chat_id in user_sessions:     
            user_sessions[chat_id]['reconnecting'] = False

# --- КОМАНДЫ ---
async def start_handler(update:   Update, context:  ContextTypes.  DEFAULT_TYPE):
    cid = update.  effective_chat.  id

    if cid in user_sessions and user_sessions[cid]['active']:
        await show_menu(update, context)
        return ConversationHandler.  END

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f).get(str(cid))
                if cfg and 'nick' in cfg and 'pass' in cfg:  
                    isok = await connect_irc_session(context.    bot, cid, cfg['nick'], cfg['pass'], cfg.    get('contacts', [DEFAULT_CHANNEL]))
                    if isok:
                        await show_menu(update, context)
                        return ConversationHandler.  END
        except Exception as e:
            logging.error(f"Start handler error: {e}")

    await update.message.reply_text("👋 Введите ваш игровой ник в Osu!")
    return NICK

async def get_nick(update, context):
    context.user_data['temp_nick'] = update.message.    text
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Получить IRC пароль", url="https://osu.ppy.sh/p/irc")]])
    await update.message.  reply_text("Теперь введите **IRC пароль**:", reply_markup=kb)
    return PASSWORD

async def get_pass(update, context):
    n, p, cid = context.user_data['temp_nick'], update.message.text, update.effective_chat.id
    save_user_data(cid, {'nick': n, 'pass': p, 'contacts': [DEFAULT_CHANNEL]})
    ok = await connect_irc_session(context.bot, cid, n, p, [DEFAULT_CHANNEL])
    if ok:
        await show_menu(update, context)
        return ConversationHandler.  END
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Получить IRC пароль", url="https://osu.ppy.sh/p/irc")]])
        await update.  message.reply_text("❌ Неверный пароль.     Введите корректный IRC пароль:", reply_markup=kb)
        return PASSWORD

async def add_handler(update:   Update, context: ContextTypes.  DEFAULT_TYPE):
    """Объединённая команда для добавления каналов и ЛС"""
    cid = update.   effective_chat.id
    u = user_sessions.get(cid)
    if not u:
        return await update.message.reply_text("❌ IRC не запущен.    /start")

    if not context.args:
        return await update.message.reply_text("📝 Использование: /add #канал или /add ник")

    target = context.args[0]
    is_channel = target.startswith('#')
    
    if is_channel:
        contact = target.   lower()
        if await send_irc_command(cid, f"JOIN {target}"):
            u['contacts'].add(contact)
            save_user_data(cid, {'contacts': list(u['contacts'])})
            await update.message.reply_text(f"✅ Присоединяюсь к каналу {target}")
        else:
            await update.message.reply_text("❌ Ошибка отправки команды")
    else:
        contact = target.lower()
        u['contacts'].add(contact)
        save_user_data(cid, {'contacts': list(u['contacts'])})
        u['target'] = contact
        await update. message.reply_text(f"✅ Добавил {target} в контакты и переключился на ЛС")
        await show_menu(update, context)

async def settings_handler(update:  Update, context: ContextTypes. DEFAULT_TYPE):
    cid = update.  effective_chat.  id
    u = user_sessions.get(cid)
    if not u:
        return await update.message.  reply_text("❌ IRC не запущен.  /start")

    show_all = u.get('show_all_messages', True)
    show_scores = u.get('show_osu_scores', True)
    send_react = u.get('send_reactions', True)
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📨 Все каналы:     {'✅ ВКЛ' if show_all else '❌ ВЫКЛ'}",
            callback_data="toggle_show_all"
        )],
        [InlineKeyboardButton(
            f"🏆 Скоры OSU:  {'✅ ВКЛ' if show_scores else '❌ ВЫКЛ'}",
            callback_data="toggle_show_scores"
        )],
        [InlineKeyboardButton(
            f"👍 Реакции:  {'✅ ВКЛ' if send_react else '❌ ВЫКЛ'}",
            callback_data="toggle_reactions"
        )],
        [InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]
    ])

    try:
        if update.callback_query:
            await update.  callback_query.message.  edit_text("⚙️ **Настройки:**", reply_markup=kb, parse_mode='Markdown')
        else:
            await update. message.reply_text("⚙️ **Настройки:**", reply_markup=kb, parse_mode='Markdown')
    except BadRequest:     
        pass

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    cid, text = update.effective_chat.id, update.message.text
    u = user_sessions.get(cid)

    if u and u.   get('target') and u.    get('active'):
        if len(text) > 500:
            await update.message.    reply_text("❌ Сообщение слишком длинное (макс 500 символов)")
            return

        # Сначала отправляем команду, потом реакцию
        send_success = await send_irc_command(cid, f"PRIVMSG {u['target']} :{text}")
        
        if send_success:
            # Отправляем реакцию только если отправка успешна
            try:
                if u.  get('send_reactions', True):
                    await update.message.set_reaction(reaction=[ReactionTypeEmoji(emoji="🕊")])
            except:
                pass
        else:
            # Отправляем ошибку в чат
            await update.message.reply_text("❌ Не удалось отправить в IRC")

    if u and u.get('show_osu_scores', True) and re.search(r'osu\. ppy\.sh/scores(/[a-z]+)?/\d+', text):
        status_msg = await update.message.reply_text("🔎")
        data = await fetch_score_v2(text)
        if data:
            bg = None
            if data['CoverUrl']:
                try:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.    get(data['CoverUrl']) as r:
                            if r.status == 200:
                                bg = await r.read()
                except:
                    pass
            photo = draw_score_card(data, bg)
            try:
                await status_msg.   delete()
                await update.message.  reply_photo(photo, caption=f"🏆 Рекорд {data['Player']}")
            except:
                pass
        else:
            try:
                await status_msg.    edit_text("❌ Не удалось получить информацию о скоре.")
            except:
                pass

async def show_menu(update, context):
    cid = update.effective_chat. id
    u = user_sessions.get(cid)
    if not u:
        return await update.message.reply_text("❌ IRC не запущен.  /start")

    kb = []
    cts = sorted(list(u['contacts']))
    dm = u.get('del_mode', False)

    for i in range(0, len(cts), 2):
        row = [InlineKeyboardButton(
            f"{'❌ ' if dm else ('✅ ' if c == u['target'] else '')}{c}",
            callback_data=f"{'del' if dm else 'set'}:{c}"
        ) for c in cts[i:    i+2]]
        kb.  append(row)

    kb.append([InlineKeyboardButton("🗑 Режим удаления:     " + ("ВКЛ" if dm else "ВЫКЛ"), callback_data="toggle_del")])
    kb.append([InlineKeyboardButton("⚙️ Настройки", callback_data="settings")])

    try:
        if update.callback_query:
            await update.callback_query.message.  edit_text(f"🎯 Чат: {u['target']}", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(f"🎯 Чат: {u['target']}", reply_markup=InlineKeyboardMarkup(kb))
    except BadRequest:
        pass

async def btn_handler(update, context):
    q = update.callback_query
    await q.answer()
    cid = update.effective_chat.id
    u = user_sessions.get(cid)

    if not u:
        return

    if q.data == "toggle_del":
        u['del_mode'] = not u['del_mode']
        await show_menu(update, context)
    elif q.data == "settings":   
        await settings_handler(update, context)
    elif q.data == "toggle_show_all":
        u['show_all_messages'] = not u.    get('show_all_messages', True)
        save_user_data(cid, {'show_all_messages': u['show_all_messages']})
        await settings_handler(update, context)
    elif q.data == "toggle_show_scores":
        u['show_osu_scores'] = not u.  get('show_osu_scores', True)
        save_user_data(cid, {'show_osu_scores': u['show_osu_scores']})
        await settings_handler(update, context)
    elif q.data == "toggle_reactions": 
        u['send_reactions'] = not u.get('send_reactions', True)
        save_user_data(cid, {'send_reactions': u['send_reactions']})
        await settings_handler(update, context)
    elif q.data == "back_to_menu":  
        await show_menu(update, context)
    elif q.data.    startswith("set:"):
        target = q.data.    split(":")[1]
        u['target'] = target
        u['del_mode'] = False
        await show_menu(update, context)
    elif q.data.  startswith("del:"):
        t = q.data.  split(":   ")[1]
        if t in u['contacts']:
            u['contacts'].remove(t)
        save_user_data(cid, {'contacts': list(u['contacts'])})
        await show_menu(update, context)

async def stop_handler(update:     Update, context: ContextTypes.    DEFAULT_TYPE):
    cid = update.effective_chat.id
    clear_user_auth(cid)
    if cid in user_sessions:  
        user_sessions[cid]['active'] = False
        try:
            user_sessions[cid]['writer']. close()
        except:
            pass
        del user_sessions[cid]
    await update.message.reply_text("🗑 Данные очищены.     Используйте /start для нового входа.")

async def post_init(app:     Application):
    await app.bot.set_my_commands([
        BotCommand("menu", "Чаты"),
        BotCommand("add", "Добавить канал/ЛС"),
        BotCommand("settings", "Настройки"),
        BotCommand("stop", "Сброс"),
        BotCommand("start", "Вход")
    ])
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                for cid, d in cfg.items():
                    if 'nick' in d and 'pass' in d:  
                        asyncio.create_task(connect_irc_session(app.  bot, int(cid), d['nick'], d['pass'], d.   get('contacts', [])))
        except Exception as e:  
            logging.error(f"Post init error: {e}")

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start_handler)],
        states={
            NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_nick)],
            PASSWORD:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pass)]
        },
        fallbacks=[]
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("menu", show_menu))
    app.add_handler(CommandHandler("add", add_handler))
    app.add_handler(CommandHandler("settings", settings_handler))
    app.add_handler(CommandHandler("stop", stop_handler))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.run_polling()

if __name__ == '__main__':
    main()
