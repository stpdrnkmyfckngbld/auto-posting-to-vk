# bot.py
import os
import requests
import vk_api
import re
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import ChatMemberUpdated
from aiogram.client.session.aiohttp import AiohttpSession
import asyncio
from collections import defaultdict
import time

# Загружаем токены из .env
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
VK_GROUP_TOKEN = os.getenv("VK_ACCESS_TOKEN")
VK_USER_TOKEN = os.getenv("VK_USER_TOKEN")
GROUP_ID = os.getenv("VK_GROUP_ID")

# Инициализация бота Telegram
session = AiohttpSession()
bot = Bot(token=TELEGRAM_BOT_TOKEN, session=session)
dp = Dispatcher()

# Хранилище для медиагрупп
media_groups = defaultdict(dict)
processed_media_groups = set()

def process_text(text):
    """Обработка текста перед публикацией в VK"""
    if not text:
        return text
    
    # 1. Удаляем посты с словом "Розыгрыш" (регистронезависимо)
    if re.search(r'розыгрыш', text, re.IGNORECASE):
        return None
    
    # 2. Заменяем @freelogistics на @freelogistics1
    text = text.replace('@freelogistics', '@freelogistics1')
    
    # 3. Извлекаем ссылки из текста (извлекаем URL из markdown ссылок)
    def replace_links(match):
        return match.group(2)  # возвращаем только URL
    
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', replace_links, text)
    
    return text

# Функция для загрузки изображения на сервер ВКонтакте
def upload_photo_to_vk(photo_url):
    try:
        response = requests.get(photo_url)
        if response.status_code == 200:
            vk_session = vk_api.VkApi(token=VK_USER_TOKEN)
            upload_url = vk_session.method("photos.getWallUploadServer", {"group_id": GROUP_ID})['upload_url']
            files = {'photo': ('photo.jpg', response.content, 'image/jpeg')}
            upload_response = requests.post(upload_url, files=files).json()

            save_response = vk_session.method("photos.saveWallPhoto", {
                'photo': upload_response['photo'],
                'server': upload_response['server'],
                'hash': upload_response['hash'],
                'group_id': GROUP_ID
            })
            return f"photo{save_response[0]['owner_id']}_{save_response[0]['id']}"
        else:
            print(f"❌ Ошибка при скачивании изображения: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Ошибка при загрузке изображения в ВКонтакте: {e}")
        return None

# Функция для публикации поста в ВКонтакте
def post_to_vk(text, photo_urls=None):
    try:
        if text is None:  # Если текст None (розыгрыш), не публикуем
            print("⏩ Пропущен пост с розыгрышем")
            return
            
        vk_session = vk_api.VkApi(token=VK_GROUP_TOKEN)
        vk = vk_session.get_api()

        attachments = []
        if photo_urls:
            for photo_url in photo_urls:
                photo_attachment = upload_photo_to_vk(photo_url)
                if photo_attachment:
                    attachments.append(photo_attachment)

        vk.wall.post(
            owner_id=f"-{GROUP_ID}",
            message=text,
            attachments=",".join(attachments) if attachments else None,
            from_group=1
        )
        print(f"✅ Пост с {len(attachments)} фото успешно опубликован в группу!")
    except Exception as e:
        print(f"❌ Ошибка при публикации поста в ВКонтакте: {e}")

# Обработчик новых сообщений в канале
@dp.channel_post()
async def handle_channel_post(message: types.Message):
    if str(message.chat.id) != TELEGRAM_CHANNEL_ID:
        return

    # Обработка медиагрупп (альбомов)
    if message.media_group_id:
        media_group_id = message.media_group_id

        # Если медиагруппа уже обработана - пропускаем
        if media_group_id in processed_media_groups:
            return

        # Если это первое сообщение медиагруппы
        if media_group_id not in media_groups:
            media_groups[media_group_id] = {
                'text': message.caption or "",
                'photos': [],
                'last_update': time.time(),
                'processed': False
            }

        # Получаем самое большое изображение
        if message.photo:
            largest_photo = message.photo[-1]
            file_info = await bot.get_file(largest_photo.file_id)
            photo_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info.file_path}"
            if photo_url not in media_groups[media_group_id]['photos']:
                media_groups[media_group_id]['photos'].append(photo_url)
                media_groups[media_group_id]['last_update'] = time.time()

        # Если медиагруппа уже обрабатывается - выходим
        if media_groups[media_group_id]['processed']:
            return

        # Помечаем медиагруппу как обрабатываемую
        media_groups[media_group_id]['processed'] = True

        # Ждем 3 секунды на случай, если придут другие части медиагруппы
        await asyncio.sleep(3)

        # Публикуем медиагруппу
        await process_media_group(media_group_id)
        return

    # Обработка одиночных постов
    original_text = message.text or message.caption or ""
    text = process_text(original_text)  # Обрабатываем текст
    
    if text is None:  # Если это розыгрыш
        print(f"⏩ Пропущен пост с розыгрышем: {original_text[:50]}...")
        return
        
    photos = []

    if message.photo:
        largest_photo = message.photo[-1]
        file_info = await bot.get_file(largest_photo.file_id)
        photo_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info.file_path}"
        photos.append(photo_url)

    if photos:
        post_to_vk(text, photos)
    else:
        post_to_vk(text)

    print(f"🔔 Пост обработан: {text[:50]}... ({len(photos)} фото)")

async def process_media_group(media_group_id):
    if media_group_id not in media_groups:
        return

    group_data = media_groups[media_group_id]
    if not group_data['photos']:
        return

    # Обрабатываем текст
    processed_text = process_text(group_data['text'])
    if processed_text is None:  # Если это розыгрыш
        print(f"⏩ Пропущена медиагруппа с розыгрышем: {group_data['text'][:50]}...")
        processed_media_groups.add(media_group_id)
        del media_groups[media_group_id]
        return

    # Публикуем весь альбом
    post_to_vk(processed_text, group_data['photos'])
    print(f"🔔 Медиагруппа обработана: {processed_text[:50]}... ({len(group_data['photos'])} фото)")

    # Помечаем медиагруппу как обработанную
    processed_media_groups.add(media_group_id)
    del media_groups[media_group_id]

async def cleanup_media_groups():
    """Очистка старых медиагрупп"""
    while True:
        await asyncio.sleep(60)
        current_time = time.time()
        # Удаляем медиагруппы, которые не обновлялись более 5 минут
        for mg_id in list(media_groups.keys()):
            if current_time - media_groups[mg_id]['last_update'] > 300:
                del media_groups[mg_id]
                print(f"🧹 Удалена старая медиагруппа {mg_id}")

async def main():
    # Удаляем вебхук перед запуском polling
    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем фоновую задачу для очистки медиагрупп
    asyncio.create_task(cleanup_media_groups())

    print("🟢 Бот запущен и ожидает новые посты в канале...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
