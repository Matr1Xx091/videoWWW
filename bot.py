import os
import asyncio
import logging
import re
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
import yt_dlp

# === ВЕРСИЯ БОТА ===
BOT_VERSION = "v2.2 (Cookies Support)"

# Настройки из переменных окружения
# ВАЖНО: Вставь сюда новый токен, старый был засвечен!
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_NEW_TOKEN_HERE")
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

user_data = {}

# === УТИЛИТЫ ===
def clean_filename(title):
    """Очистка имени файла от спецсимволов"""
    clean = re.sub(r'[^\w\s\-]', '', str(title))
    return clean.strip()[:100]

def get_platform(url):
    """Определение платформы по URL"""
    url_lower = url.lower()
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    return 'other'

# === WEB SERVER ===
async def health_check(request):
    return web.Response(text="✅ Bot is running!")

async def start_web_server():
    """Веб-сервер для Render"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"🌐 Web server started on port {PORT}")

# === КЛАВИАТУРЫ ===
def get_quality_keyboard():
    """Клавиатура выбора формата"""
    buttons = [
        [
            InlineKeyboardButton(text="📹 Видео (Лучшее)", callback_data="quality_video_best"),
            InlineKeyboardButton(text="🎬 Видео (720p)", callback_data="quality_video_720")
        ],
        [
            InlineKeyboardButton(text="📱 Видео (480p)", callback_data="quality_video_480"),
            InlineKeyboardButton(text="🎵 Только аудио", callback_data="quality_audio")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# === ПРОГРЕСС ЗАГРУЗКИ ===
class ProgressTracker:
    def __init__(self, chat_id, message):
        self.chat_id = chat_id
        self.message = message
        self.last_percent = 0
        self.last_update = 0
        
    def hook(self, d):
        """Хук для отслеживания прогресса yt-dlp"""
        if d['status'] == 'downloading':
            try:
                percent = d.get('_percent_str', '0%').replace('%', '')
                percent = float(percent)
                current_time = asyncio.get_event_loop().time()
                
                # Обновляем только если изменение > 5% или прошло > 3 сек
                if abs(percent - self.last_percent) > 5 or (current_time - self.last_update) > 3:
                    self.last_percent = percent
                    self.last_update = current_time
                    asyncio.create_task(self.update_progress(percent))
            except:
                pass
    
    async def update_progress(self, percent):
        """Обновление сообщения с прогрессом"""
        try:
            bar_length = 10
            filled = int(bar_length * percent / 100)
            bar = '█' * filled + '░' * (bar_length - filled)
            text = f"📥 <b>Загрузка...</b>\n{bar} {int(percent)}%"
            await self.message.edit_text(text)
        except:
            pass

# === ЗАГРУЗКА ВИДЕО ===
async def download_video(url, chat_id, quality_mode, status_msg):
    """Основная функция загрузки"""
    platform = get_platform(url)
    downloads_dir = 'downloads'
    os.makedirs(downloads_dir, exist_ok=True)
    
    # Шаблон для имени файла
    filename_template = f'{downloads_dir}/{chat_id}_%(title)s.%(ext)s'
    
    # === НАСТРОЙКА КУКИ ===
    # Проверяем, есть ли файл cookies.txt в папке с ботом
    cookie_file = 'cookies.txt'
    use_cookies = os.path.exists(cookie_file)
    if use_cookies:
        logging.info("🍪 Found cookies.txt, using it for authentication.")
    else:
        logging.warning("⚠️ cookies.txt not found. YouTube might block requests.")

    # Базовые настройки yt-dlp
    ydl_opts = {
        'outtmpl': filename_template,
        'quiet': False,
        'no_warnings': False,
        'noplaylist': True,
        'extractor_retries': 3,
        'fragment_retries': 3,
        # Если есть куки - добавляем их
        'cookiefile': cookie_file if use_cookies else None,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    # Настройка формата в зависимости от выбора
    if quality_mode == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    elif quality_mode == 'video_best':
        ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    elif quality_mode == 'video_720':
        ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
    elif quality_mode == 'video_480':
        ydl_opts['format'] = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best'
    
    # Специфичные настройки для платформ
    if platform == 'youtube':
        ydl_opts.update({
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios', 'web', 'android'],
                    'skip': ['dash', 'hls']
                }
            },
            # Обход бота-детектора YouTube (даже с куками полезно)
            'http_headers': {
                'User-Agent': 'com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            }
        })
    elif platform == 'tiktok':
        ydl_opts['http_headers'] = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.tiktok.com/'
        }
    
    # Прогресс-хук
    tracker = ProgressTracker(chat_id, status_msg)
    ydl_opts['progress_hooks'] = [tracker.hook]
    
    try:
        await status_msg.edit_text("🔍 <b>Получаю информацию...</b>")
        
        loop = asyncio.get_event_loop()
        
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info, ydl.prepare_filename(info)
        
        # Попытка скачать
        try:
            info, downloaded_file = await loop.run_in_executor(None, download)
        except Exception as first_error:
            # Если ошибка и это YouTube - пробуем упрощенный метод (тоже с куками)
            if platform == 'youtube':
                await status_msg.edit_text("🔄 <b>Пробую альтернативный метод...</b>")
                
                ydl_opts_fallback = {
                    'outtmpl': filename_template,
                    'quiet': True,
                    'no_warnings': True,
                    'format': 'best[ext=mp4]/best' if quality_mode != 'audio' else 'bestaudio/best',
                    # Добавляем куки и сюда
                    'cookiefile': cookie_file if use_cookies else None,
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android'],
                        }
                    },
                }
                
                if quality_mode == 'audio':
                    ydl_opts_fallback['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
                
                def download_fallback():
                    with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl:
                        info = ydl.extract_info(url, download=True)
                        return info, ydl.prepare_filename(info)
                
                info, downloaded_file = await loop.run_in_executor(None, download_fallback)
            else:
                raise first_error
        
        # Поиск скачанного файла
        base_name = os.path.splitext(downloaded_file)[0]
        possible_files = [
            downloaded_file,
            base_name + '.mp4',
            base_name + '.mp3',
            base_name + '.webm',
            base_name + '.mkv'
        ]
        
        final_file = None
        for file_path in possible_files:
            if os.path.exists(file_path):
                final_file = file_path
                break
        
        if not final_file or not os.path.exists(final_file):
            raise Exception("Файл не найден после скачивания")
        
        # Проверка размера
        file_size = os.path.getsize(final_file) / (1024 * 1024)  # MB
        
        if file_size > 50:
            await status_msg.edit_text(
                f"⚠️ <b>Файл слишком большой!</b>\n"
                f"Размер: {file_size:.1f} МБ\n"
                f"Лимит Telegram: 50 МБ\n\n"
                f"💡 Попробуй выбрать качество ниже"
            )
            os.remove(final_file)
            return
        
        # Отправка файла
        await status_msg.edit_text("📤 <b>Отправляю...</b>")
        
        title = clean_filename(info.get('title', 'video'))
        caption = f"📺 <b>{title}</b>\n💾 {file_size:.1f} МБ"
        
        if quality_mode == 'audio':
            await bot.send_audio(
                chat_id,
                FSInputFile(final_file),
                caption=caption,
                title=title
            )
        else:
            await bot.send_video(
                chat_id,
                FSInputFile(final_file),
                caption=caption,
                supports_streaming=True
            )
        
        await status_msg.delete()
        
        # Очистка
        if os.path.exists(final_file):
            os.remove(final_file)
        
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        
        if 'Sign in' in error_msg:
             await status_msg.edit_text(
                "🔐 <b>Требуется авторизация</b>\n\n"
                "YouTube заблокировал доступ.\n"
                "Администратору нужно обновить файл <code>cookies.txt</code>."
            )
        elif '429' in error_msg:
            await status_msg.edit_text("⛔️ <b>Слишком много запросов</b>\nYouTube временно заблокировал IP.")
        elif 'Private video' in error_msg:
            await status_msg.edit_text("🔒 <b>Приватное видео</b>\nДоступ закрыт")
        else:
            await status_msg.edit_text(f"❌ <b>Ошибка загрузки</b>\n\n<code>{error_msg[:200]}</code>")
        
    except Exception as e:
        logging.error(f"Error downloading: {e}")
        await status_msg.edit_text(f"⚠️ <b>Ошибка:</b>\n<code>{str(e)[:200]}</code>")
    
    finally:
        # Очистка всех временных файлов
        try:
            for f in os.listdir(downloads_dir):
                if str(chat_id) in f:
                    file_path = os.path.join(downloads_dir, f)
                    if os.path.exists(file_path):
                        os.remove(file_path)
        except:
            pass

# === ОБРАБОТЧИКИ ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"👋 <b>Привет!</b>\n\n"
        f"🤖 Версия: <code>{BOT_VERSION}</code>\n"
        "Отправь мне ссылку на видео (YouTube, TikTok, Instagram)."
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    has_cookies = os.path.exists('cookies.txt')
    await message.answer(
        f"📊 <b>Статус</b>\n"
        f"🍪 Cookies.txt: {'✅ Загружен' if has_cookies else '❌ Отсутствует'}\n"
        f"Если YouTube не работает — обнови куки."
    )

@dp.message(F.text)
async def process_link(message: types.Message):
    url = message.text.strip()
    
    if not url.startswith(('http://', 'https://')):
        await message.answer("❌ Это не ссылка.")
        return
    
    user_data[message.from_user.id] = url
    
    await message.answer(
        "🎬 Выбери качество:",
        reply_markup=get_quality_keyboard()
    )

@dp.callback_query(F.data.startswith("quality_"))
async def process_quality(callback: types.CallbackQuery):
    url = user_data.get(callback.from_user.id)
    if not url:
        await callback.message.edit_text("❌ Ссылка устарела.")
        return
    
    parts = callback.data.split("_")
    quality_mode = f"{parts[1]}_{parts[2]}" if len(parts) >= 3 else parts[1]
    
    status_msg = await callback.message.edit_text("⏳ <b>Начинаю загрузку...</b>")
    await download_video(url, callback.message.chat.id, quality_mode, status_msg)
    user_data.pop(callback.from_user.id, None)

# === MAIN ===
async def main():
    os.makedirs('downloads', exist_ok=True)
    
    # Проверка куки при запуске
    if os.path.exists('cookies.txt'):
        logging.info("🍪 Cookies.txt detected! YouTube support enabled.")
    else:
        logging.warning("⚠️ Cookies.txt NOT found. YouTube videos might fail.")

    asyncio.create_task(start_web_server())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
