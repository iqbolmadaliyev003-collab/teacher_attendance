"""O'qituvchilar davomat boti — barcha kod shu bitta faylda.
Sozlamalar config.py faylida turadi.

Fayl tuzilishi (bo'limlar):
  1. MA'LUMOTLAR BAZASI  — SQLite bilan ishlash
  2. YORDAMCHI FUNKSIYALAR — masofa, vaqt formati, admin tekshiruvi
  3. KLAVIATURALAR — tugmalar
  4. /START — hamma uchun kirish nuqtasi
  5. O'QITUVCHI QISMI — keldim, lokatsiya, statistika, bonus/jazolarim
  6. ADMIN PANELI — tugmalar orqali boshqarish (qadam-baqadam)
  7. PDF HISOBOT — davomat + bonus/jazo sabablari bilan
  8. ADMIN BUYRUQLARI — eski matnli buyruqlar (ixtiyoriy)
  9. ISHGA TUSHIRISH
"""

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from math import atan2, ceil, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    User,
)
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from config import (
    ADMIN_IDS,
    BOT_TOKEN,
    CENTER_LATITUDE,
    CENTER_LONGITUDE,
    DB_PATH,
    EARLY_REQUIRED_MINUTES,
    FINE_EARLY_PER_MINUTE,
    FINE_LATE_PER_MINUTE,
    GROUP_CHAT_ID,
    RADIUS_METERS,
    TIMEZONE,
)

logger = logging.getLogger(__name__)
TZ = ZoneInfo(TIMEZONE)

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ALREADY_CHECKED = "Siz bugun allaqachon kelganingizni belgilagansiz. ✅"

# Hafta kunlari — Python'ning weekday() tartibida: 0 = dushanba ... 6 = yakshanba
WEEKDAYS = [
    "Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba",
]

# Yangi o'qituvchi qo'shilganda beriladigan standart ketish vaqti
DEFAULT_DEPARTURE = "18:00"

# Jazo uchun tayyor sabablar — admin ro'yxatdan tanlaydi
JAZO_REASONS = [
    "Rangli ichimlik yoki xidli mahsulot iste'mol qilish",
    "Uniforma kiymaganligi",
    "Ish vaqtida mobil qurilmalardan foydalanish",
]


# ==================== 1. MA'LUMOTLAR BAZASI ====================

def db(query: str, params=(), fetch: str | None = None):
    """Barcha SQL so'rovlar uchun bitta yordamchi funksiya.
    fetch="one" — bitta qator, fetch="all" — hamma qatorlar, aks holda rowcount."""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(query, params)
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        else:
            result = cur.rowcount
        conn.commit()
        return result


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS teachers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                scheduled_time TEXT NOT NULL  -- 'HH:MM'
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                arrived_time TEXT NOT NULL,
                is_late INTEGER NOT NULL,
                late_minutes INTEGER NOT NULL,          -- belgilangan vaqtdan keyingi daqiqalar (7000)
                early_minutes INTEGER NOT NULL DEFAULT 0,-- deadline bilan belgilangan vaqt orasidagi daqiqalar (5000)
                fine_amount INTEGER NOT NULL DEFAULT 0,  -- jami jarima (so'mda)
                scheduled_time TEXT,                     -- o'sha kunga amal qilgan belgilangan vaqt
                FOREIGN KEY (teacher_id) REFERENCES teachers (id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_teacher_date
                ON attendance (teacher_id, attendance_date);

            -- Haftalik jadval: har bir kun uchun alohida kelish/ketish vaqti.
            -- Qator yo'q  -> o'sha kunga o'qituvchining standart vaqti ishlatiladi.
            -- arrive_time NULL -> o'sha kun dam olish kuni deb belgilangan.
            CREATE TABLE IF NOT EXISTS schedules (
                teacher_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL,     -- 0 = dushanba ... 6 = yakshanba
                arrive_time TEXT,             -- 'HH:MM' yoki NULL (dam olish kuni)
                leave_time TEXT,              -- 'HH:MM' yoki NULL
                PRIMARY KEY (teacher_id, weekday),
                FOREIGN KEY (teacher_id) REFERENCES teachers (id)
            );

            -- Bonus va jazolar
            CREATE TABLE IF NOT EXISTS marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                mark_type TEXT NOT NULL,      -- 'bonus' yoki 'jazo'
                reason TEXT NOT NULL,
                mark_date TEXT NOT NULL,      -- 'YYYY-MM-DD'
                created_at TEXT NOT NULL,     -- 'HH:MM:SS'
                admin_id INTEGER NOT NULL,
                FOREIGN KEY (teacher_id) REFERENCES teachers (id)
            );
            CREATE INDEX IF NOT EXISTS idx_marks_teacher_date
                ON marks (teacher_id, mark_date);
            """
        )
        # Eski bazalarda bu ustunlar yo'q edi — bor bo'lmasa qo'shamiz
        teacher_columns = {row[1] for row in conn.execute("PRAGMA table_info(teachers)")}
        if "departure_time" not in teacher_columns:
            # DDL'da "?" ishlamaydi, shuning uchun qiymat to'g'ridan-to'g'ri yoziladi
            conn.execute(
                "ALTER TABLE teachers ADD COLUMN departure_time TEXT NOT NULL "
                f"DEFAULT '{DEFAULT_DEPARTURE}'"
            )

        # Eski attendance jadvaliga yangi ustunlarni qo'shamiz (bor bo'lmasa)
        attendance_columns = {row[1] for row in conn.execute("PRAGMA table_info(attendance)")}
        if "scheduled_time" not in attendance_columns:
            # o'sha kunga amal qilgan vaqt — jadval keyin o'zgarsa ham eski hisobot to'g'ri qoladi
            conn.execute("ALTER TABLE attendance ADD COLUMN scheduled_time TEXT")
        if "early_minutes" not in attendance_columns:
            conn.execute("ALTER TABLE attendance ADD COLUMN early_minutes INTEGER NOT NULL DEFAULT 0")
        if "fine_amount" not in attendance_columns:
            conn.execute("ALTER TABLE attendance ADD COLUMN fine_amount INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def today() -> str:
    """Joriy sana — config'dagi vaqt zonasi bo'yicha (server UTC bo'lsa ham to'g'ri)."""
    return datetime.now(TZ).date().isoformat()


def get_teacher(telegram_id: int):
    """(id, telegram_id, ism, familiya, kelish_vaqti, ketish_vaqti) yoki None."""
    return db(
        "SELECT id, telegram_id, first_name, last_name, scheduled_time, departure_time "
        "FROM teachers WHERE telegram_id = ?",
        (telegram_id,), fetch="one",
    )


def add_teacher(
    telegram_id: int, first_name: str, last_name: str,
    sched_time: str, departure: str = DEFAULT_DEPARTURE,
) -> bool:
    try:
        db(
            "INSERT INTO teachers "
            "(telegram_id, first_name, last_name, scheduled_time, departure_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (telegram_id, first_name, last_name, sched_time, departure),
        )
        return True
    except sqlite3.IntegrityError:  # bu telegram_id allaqachon mavjud
        return False


# ---------- Haftalik jadval ----------

def get_day_schedule(teacher_id: int, weekday: int):
    """Shu hafta kuni uchun (kelish, ketish) juftligi.
    None — bu kunga alohida jadval yo'q (standart vaqt ishlatiladi).
    Kelish None bo'lsa — kun dam olish kuni deb belgilangan."""
    return db(
        "SELECT arrive_time, leave_time FROM schedules WHERE teacher_id = ? AND weekday = ?",
        (teacher_id, weekday), fetch="one",
    )


def get_week_schedule(teacher_id: int) -> dict[int, tuple]:
    """{hafta_kuni: (kelish, ketish)} — faqat belgilangan kunlar."""
    rows = db(
        "SELECT weekday, arrive_time, leave_time FROM schedules WHERE teacher_id = ?",
        (teacher_id,), fetch="all",
    )
    return {weekday: (arrive, leave) for weekday, arrive, leave in rows}


def set_day_schedule(teacher_id: int, weekday: int, arrive: str | None, leave: str | None):
    db(
        "INSERT INTO schedules (teacher_id, weekday, arrive_time, leave_time) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(teacher_id, weekday) DO UPDATE SET arrive_time = ?, leave_time = ?",
        (teacher_id, weekday, arrive, leave, arrive, leave),
    )


def clear_week_schedule(teacher_id: int) -> int:
    return db("DELETE FROM schedules WHERE teacher_id = ?", (teacher_id,))


def times_for_day(teacher, when: datetime) -> tuple[str | None, str | None]:
    """O'sha kunga amal qiladigan (kelish, ketish) vaqtlari.
    Haftalik jadvalda qator bo'lsa — o'sha, aks holda standart vaqtlar."""
    day = get_day_schedule(teacher[0], when.weekday())
    if day is not None:
        return day[0], day[1]
    return teacher[4], teacher[5]


# ---------- Bonus va jazolar ----------

def add_mark(teacher_id: int, mark_type: str, reason: str, admin_id: int):
    now = datetime.now(TZ)
    db(
        "INSERT INTO marks (teacher_id, mark_type, reason, mark_date, created_at, admin_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (teacher_id, mark_type, reason, now.date().isoformat(),
         now.strftime("%H:%M:%S"), admin_id),
    )


def get_marks(teacher_id: int, date_from=None, date_to=None):
    """(turi, sabab, sana) ro'yxati — eng yangisi birinchi."""
    query = "SELECT mark_type, reason, mark_date FROM marks WHERE teacher_id = ?"
    params: list = [teacher_id]
    if date_from and date_to:
        query += " AND mark_date BETWEEN ? AND ?"
        params += [date_from.isoformat(), date_to.isoformat()]
    return db(query + " ORDER BY mark_date DESC, id DESC", tuple(params), fetch="all")


def record_attendance(
    teacher_id: int, arrived: str, is_late: bool, late_min: int,
    early_min: int, fine_amount: int, scheduled: str | None,
) -> bool:
    """Davomatni yozadi; bugun allaqachon yozuv bo'lsa False qaytaradi."""
    return db(
        "INSERT OR IGNORE INTO attendance "
        "(teacher_id, attendance_date, arrived_time, is_late, late_minutes, "
        "early_minutes, fine_amount, scheduled_time) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (teacher_id, today(), arrived, int(is_late), late_min,
         early_min, fine_amount, scheduled),
    ) > 0


def has_checked_in_today(teacher_id: int) -> bool:
    return db(
        "SELECT 1 FROM attendance WHERE teacher_id = ? AND attendance_date = ?",
        (teacher_id, today()), fetch="one",
    ) is not None


# ==================== 2. YORDAMCHI FUNKSIYALAR ====================

def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Ikki geografik nuqta orasidagi masofa, metrlarda (Haversine formulasi)."""
    p1, p2 = radians(lat1), radians(lat2)
    a = (
        sin(radians(lat2 - lat1) / 2) ** 2
        + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    )
    return 6371000 * 2 * atan2(sqrt(a), sqrt(1 - a))


def format_minutes(total_minutes: int) -> str:
    """123 -> '2 soat 3 daqiqa', 45 -> '45 daqiqa'."""
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours} soat {minutes} daqiqa" if hours else f"{minutes} daqiqa"


def format_money(amount: int) -> str:
    """15000 -> '15 000 so'm'."""
    return f"{amount:,}".replace(",", " ") + " so'm"


def compute_fine(now: datetime, scheduled_dt: datetime) -> tuple[int, int, int]:
    """Kelgan vaqtga qarab jarimani hisoblaydi.
    Qaytaradi: (erta_oyna_daqiqasi, kechikkan_daqiqa, jami_jarima).

    Ustoz belgilangan vaqtdan EARLY_REQUIRED_MINUTES daqiqa oldin kelishi kerak:
      • deadline (masalan 08:55) gacha kelsa — jarima yo'q;
      • deadline bilan belgilangan vaqt (08:55–09:00) orasidagi har daqiqa — 5000 so'm;
      • belgilangan vaqtdan (09:00) keyingi har daqiqa — 7000 so'm.
    30 soniya ham 1 daqiqa deb hisoblanadi (yaxlitlash yuqoriga)."""
    deadline = scheduled_dt - timedelta(minutes=EARLY_REQUIRED_MINUTES)
    if now <= deadline:
        return 0, 0, 0

    # Erta kelish oynasi: deadline dan belgilangan vaqtgacha (yoki kelgan vaqtgacha) bo'lgan qism
    early_seconds = (min(now, scheduled_dt) - deadline).total_seconds()
    early_min = ceil(early_seconds / 60) if early_seconds > 0 else 0

    # Belgilangan vaqtdan keyingi qism
    late_seconds = (now - scheduled_dt).total_seconds()
    late_min = ceil(late_seconds / 60) if late_seconds > 0 else 0

    total = early_min * FINE_EARLY_PER_MINUTE + late_min * FINE_LATE_PER_MINUTE
    return early_min, late_min, total


def is_admin(user: User | None) -> bool:
    return user is not None and user.id in ADMIN_IDS


def format_week_schedule(teacher) -> str:
    """O'qituvchining haftalik jadvalini o'qiladigan matnga aylantiradi."""
    week = get_week_schedule(teacher[0])
    if not week:
        return (
            f"Haftalik jadval belgilanmagan — har kuni standart vaqt:\n"
            f"🕘 Kelish: {teacher[4]}   🕕 Ketish: {teacher[5]}"
        )

    lines = []
    for weekday, name in enumerate(WEEKDAYS):
        if weekday in week:
            arrive, leave = week[weekday]
            if arrive is None:
                lines.append(f"• {name}: 🌙 dam olish kuni")
            else:
                lines.append(f"• {name}: 🕘 {arrive} — 🕕 {leave or '—'}")
        else:
            lines.append(f"• {name}: {teacher[4]} — {teacher[5]} (standart)")
    return "\n".join(lines)


def not_registered_text(user_id: int) -> str:
    """Ro'yxatda yo'q foydalanuvchiga o'z IDsini ko'rsatamiz —
    shu ID orqali admin uni osongina qo'shadi."""
    return (
        "Siz tizimda ro'yxatdan o'tmagansiz.\n\n"
        f"🆔 Sizning Telegram ID raqamingiz: <code>{user_id}</code>\n"
        "Shu raqamni administratorga yuboring — u sizni ro'yxatga qo'shadi."
    )


# ==================== 3. KLAVIATURALAR ====================

def menu_kb(user: User) -> ReplyKeyboardMarkup | None:
    """Foydalanuvchi kimligiga qarab asosiy menyu tugmalari:
    o'qituvchiga — Keldim/Statistika, adminga — boshqaruv tugmalari."""
    rows = []
    if get_teacher(user.id):
        rows.append([KeyboardButton(text="✅ Keldim")])
        rows.append([
            KeyboardButton(text="📊 Statistikam"),
            KeyboardButton(text="🏅 Bonus va jazolarim"),
        ])
    if is_admin(user):
        rows.append([
            KeyboardButton(text="➕ O'qituvchi qo'shish"),
            KeyboardButton(text="📋 O'qituvchilar ro'yxati"),
        ])
        rows.append([
            KeyboardButton(text="🏅 Bonus berish"),
            KeyboardButton(text="⚠️ Jazo berish"),
        ])
        rows.append([KeyboardButton(text="📄 PDF hisobot")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True) if rows else None


def teachers_pick_kb(prefix: str) -> InlineKeyboardMarkup | None:
    """Barcha o'qituvchilar ro'yxatidan bittasini tanlash uchun tugmalar."""
    teachers = db(
        "SELECT first_name, last_name, telegram_id FROM teachers ORDER BY first_name, last_name",
        fetch="all",
    )
    if not teachers:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{first} {last}", callback_data=f"{prefix}:{tg_id}")]
        for first, last, tg_id in teachers
    ])


CANCEL_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
    resize_keyboard=True,
)

LOCATION_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

STATS_KB = InlineKeyboardMarkup(
    inline_keyboard=[[
        InlineKeyboardButton(text="📅 Bugun", callback_data="stats_day"),
        InlineKeyboardButton(text="🗓 Bu hafta", callback_data="stats_week"),
        InlineKeyboardButton(text="📆 Bu oy", callback_data="stats_month"),
    ]]
)

REPORT_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📆 Shu oy", callback_data="rep_this"),
            InlineKeyboardButton(text="🗓 O'tgan oy", callback_data="rep_prev"),
        ],
        [InlineKeyboardButton(text="✏️ Boshqa davr", callback_data="rep_custom")],
    ]
)

MARKS_KB = InlineKeyboardMarkup(
    inline_keyboard=[[
        InlineKeyboardButton(text="🏅 Bonuslar", callback_data="my_bonus"),
        InlineKeyboardButton(text="⚠️ Jazolar", callback_data="my_jazo"),
    ]]
)


def schedule_kb(selected: set[int]) -> InlineKeyboardMarkup:
    """Haftalik jadval muharriri: kunlarni belgilash + amallar."""
    rows = []
    for start in range(0, 7, 2):
        rows.append([
            InlineKeyboardButton(
                text=f"{'☑️' if weekday in selected else '☐'} {WEEKDAYS[weekday]}",
                callback_data=f"sday:{weekday}",
            )
            for weekday in range(start, min(start + 2, 7))
        ])
    rows.append([InlineKeyboardButton(text="⏰ Tanlangan kunlarga vaqt belgilash", callback_data="sset")])
    rows.append([InlineKeyboardButton(text="🌙 Tanlangan kunlar — dam olish", callback_data="soff")])
    rows.append([InlineKeyboardButton(text="🗑 Jadvalni tozalash", callback_data="sclear")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Routerlar: admin buyruqlari birinchi, keyin admin paneli, keyin o'qituvchi qismi
admin_router = Router()

panel_router = Router()
panel_router.message.filter(F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
panel_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

teacher_router = Router()
# Guruhdagi "Keldim" yoki lokatsiya xabarlariga javob bermasligi uchun faqat shaxsiy chat
teacher_router.message.filter(F.chat.type == "private")


# ==================== 4. /START ====================

@teacher_router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    teacher = get_teacher(user.id)
    kb = menu_kb(user)

    if is_admin(user):
        text = (
            "Assalomu alaykum, admin! 👋\n\n"
            "Pastdagi tugmalar orqali botni boshqarasiz:\n\n"
            "➕ <b>O'qituvchi qo'shish</b> — bot hamma narsani qadam-baqadam so'raydi\n"
            "📋 <b>O'qituvchilar ro'yxati</b> — vaqt, haftalik jadval, o'chirish\n"
            "🏅 <b>Bonus berish</b> — o'qituvchini tanlab, sababini yozasiz\n"
            "⚠️ <b>Jazo berish</b> — o'qituvchini tanlab, sababini ro'yxatdan tanlaysiz\n"
            "📄 <b>PDF hisobot</b> — davomat, bonus va jazolar bitta faylda"
        )
        if teacher:
            text += "\n\nSiz o'qituvchi sifatida ham ro'yxatdasiz — \"✅ Keldim\" tugmasi ham ishlaydi."
        await message.answer(text, reply_markup=kb)
        return

    if not teacher:
        await message.answer("Assalomu alaykum! " + not_registered_text(user.id))
        return

    first_name, last_name = teacher[2], teacher[3]
    await message.answer(
        f"Assalomu alaykum, {first_name} {last_name}!\n\n"
        "Markazga yetib kelganingizda pastdagi \"✅ Keldim\" tugmasini bosing.\n\n"
        f"<b>Sizning ish jadvalingiz:</b>\n{format_week_schedule(teacher)}",
        reply_markup=kb,
    )


# ==================== 5. O'QITUVCHI QISMI ====================

@teacher_router.message(F.text == "✅ Keldim")
async def handle_keldim(message: Message):
    teacher = get_teacher(message.from_user.id)
    if not teacher:
        await message.answer(not_registered_text(message.from_user.id))
        return

    if has_checked_in_today(teacher[0]):
        await message.answer(ALREADY_CHECKED)
        return

    await message.answer("Iltimos, joriy lokatsiyangizni yuboring 👇", reply_markup=LOCATION_KB)


@teacher_router.message(F.location)
async def handle_location(message: Message):
    teacher = get_teacher(message.from_user.id)
    if not teacher:
        await message.answer(not_registered_text(message.from_user.id))
        return

    teacher_id, first_name, last_name = teacher[0], teacher[2], teacher[3]

    if has_checked_in_today(teacher_id):
        await message.answer(ALREADY_CHECKED)
        return

    # Boshqa joydan forward qilingan (eski) lokatsiyani qabul qilmaymiz
    if message.forward_origin is not None:
        await message.answer(
            "❌ Forward qilingan lokatsiya qabul qilinmaydi.\n"
            "Iltimos, \"📍 Lokatsiyani yuborish\" tugmasi orqali joriy "
            "lokatsiyangizni yuboring.",
            reply_markup=LOCATION_KB,
        )
        return

    dist = distance_meters(
        message.location.latitude, message.location.longitude,
        CENTER_LATITUDE, CENTER_LONGITUDE,
    )
    if dist > RADIUS_METERS:
        await message.answer(
            "❌ Siz hali o'quv markazga yetib kelmagansiz.\n"
            f"Markazgacha bo'lgan masofa: taxminan {int(dist)} metr.\n"
            "Markazga yetib kelganingizdan so'ng qaytadan urinib ko'ring.",
            reply_markup=menu_kb(message.from_user),
        )
        return

    now = datetime.now(TZ)
    arrived = now.strftime("%H:%M:%S")

    # Shu hafta kuniga belgilangan vaqt (haftalik jadval bo'lsa — o'sha, aks holda standart)
    scheduled_time, leave_time = times_for_day(teacher, now)

    if scheduled_time is None:
        # Bu kun dam olish kuni deb belgilangan — kechikish/jarima hisoblanmaydi
        early_minutes = late_minutes = fine_amount = 0
    else:
        sched_hour, sched_minute = map(int, scheduled_time.split(":"))
        scheduled_dt = now.replace(hour=sched_hour, minute=sched_minute, second=0, microsecond=0)
        early_minutes, late_minutes, fine_amount = compute_fine(now, scheduled_dt)

    is_late = late_minutes > 0

    if not record_attendance(teacher_id, arrived, is_late, late_minutes,
                             early_minutes, fine_amount, scheduled_time):
        await message.answer(ALREADY_CHECKED)
        return

    # Guruhga yuboriladigan xabar
    group_text = f"👤 {first_name} {last_name}\n🕒 Kelgan vaqti: {arrived}\n"
    if scheduled_time is None:
        group_text += "🌙 Bugun dam olish kuni sifatida belgilangan"
    elif fine_amount == 0:
        group_text += f"⏰ Belgilangan vaqt: {scheduled_time}\n🟢 Vaqtida keldi"
    else:
        group_text += f"⏰ Belgilangan vaqt: {scheduled_time}\n"
        if early_minutes:
            group_text += (
                f"🟡 Erta kelish oynasida {early_minutes} daqiqa kechikdi "
                f"→ {format_money(early_minutes * FINE_EARLY_PER_MINUTE)}\n"
            )
        if late_minutes:
            group_text += (
                f"🔴 Belgilangan vaqtdan {format_minutes(late_minutes)} kech qoldi "
                f"→ {format_money(late_minutes * FINE_LATE_PER_MINUTE)}\n"
            )
        group_text += f"💰 Jami jarima: <b>{format_money(fine_amount)}</b>"

    try:
        await message.bot.send_message(GROUP_CHAT_ID, group_text)
    except TelegramAPIError:
        logger.exception("Guruhga (%s) xabar yuborib bo'lmadi", GROUP_CHAT_ID)

    reply = "✅ Kelganingiz muvaffaqiyatli qayd etildi. Rahmat!"
    if fine_amount:
        reply += f"\n💰 Bugungi jarima: <b>{format_money(fine_amount)}</b>"
    if leave_time:
        reply += f"\n🕕 Markazdan ketish vaqtingiz: <b>{leave_time}</b>"
    await message.answer(reply, reply_markup=menu_kb(message.from_user))


@teacher_router.message(F.text == "📊 Statistikam")
async def handle_stats_menu(message: Message):
    if not get_teacher(message.from_user.id):
        await message.answer(not_registered_text(message.from_user.id))
        return

    await message.answer("Qaysi davr uchun statistikani ko'rmoqchisiz?", reply_markup=STATS_KB)


@teacher_router.callback_query(F.data.in_({"stats_day", "stats_week", "stats_month"}))
async def handle_stats_callback(callback: CallbackQuery):
    teacher = get_teacher(callback.from_user.id)
    if not teacher:
        await callback.answer("Siz ro'yxatdan o'tmagansiz.", show_alert=True)
        return

    today_date = datetime.now(TZ).date()
    if callback.data == "stats_day":
        date_from, period_label = today_date, "Bugungi"
    elif callback.data == "stats_week":
        date_from = today_date - timedelta(days=today_date.weekday())  # shu haftaning dushanbasi
        period_label = "Shu haftadagi"
    else:  # stats_month
        date_from, period_label = today_date.replace(day=1), "Shu oydagi"

    records = db(
        "SELECT is_late, late_minutes, fine_amount FROM attendance "
        "WHERE teacher_id = ? AND attendance_date BETWEEN ? AND ?",
        (teacher[0], date_from.isoformat(), today_date.isoformat()), fetch="all",
    )

    total_days = len(records)
    late_count = sum(1 for is_late, _, _ in records if is_late)
    total_late_minutes = sum(m for is_late, m, _ in records if is_late)
    fined_days = sum(1 for _, _, fine in records if fine)
    total_fine = sum(fine for _, _, fine in records)

    if total_days == 0:
        text = f"📊 {period_label} statistika:\n\nBu davrda davomat yozuvi topilmadi."
    else:
        text = (
            f"📊 {period_label} statistika:\n\n"
            f"✅ Jami kelgan kunlar: {total_days}\n"
            f"🔴 Kech qolgan kunlar: {late_count}\n"
        )
        if late_count:
            text += f"⏰ Jami kechikish: {format_minutes(total_late_minutes)}\n"
        if total_fine:
            text += (
                f"💰 Jarimali kunlar: {fined_days}\n"
                f"💰 Jami jarima: <b>{format_money(total_fine)}</b>"
            )
        else:
            text += "🎉 Jarima yo'q — baraka toping!"

    try:
        await callback.message.edit_text(text)
    except TelegramBadRequest:
        pass  # bir xil tugma ikki marta bosilsa "message is not modified" xatosi chiqadi
    await callback.answer()


# ---------- O'qituvchining bonus va jazolari ----------

@teacher_router.message(F.text == "🏅 Bonus va jazolarim")
async def handle_my_marks(message: Message):
    teacher = get_teacher(message.from_user.id)
    if not teacher:
        await message.answer(not_registered_text(message.from_user.id))
        return

    marks = get_marks(teacher[0])
    bonus_count = sum(1 for mark_type, _, _ in marks if mark_type == "bonus")
    jazo_count = len(marks) - bonus_count

    await message.answer(
        f"🏅 Jami bonuslar: <b>{bonus_count}</b> ta\n"
        f"⚠️ Jami jazolar: <b>{jazo_count}</b> ta\n\n"
        "Batafsil ko'rish uchun tugmani bosing:",
        reply_markup=MARKS_KB,
    )


@teacher_router.callback_query(F.data.in_({"my_bonus", "my_jazo"}))
async def handle_my_marks_detail(callback: CallbackQuery):
    teacher = get_teacher(callback.from_user.id)
    if not teacher:
        await callback.answer("Siz ro'yxatdan o'tmagansiz.", show_alert=True)
        return

    wanted = "bonus" if callback.data == "my_bonus" else "jazo"
    title = "🏅 Bonuslaringiz" if wanted == "bonus" else "⚠️ Jazolaringiz"
    rows = [(reason, date) for mark_type, reason, date in get_marks(teacher[0])
            if mark_type == wanted]

    if not rows:
        text = f"{title}\n\nHozircha bunday yozuv yo'q."
        if wanted == "jazo":
            text += " Shunday davom eting! 🎉"
    else:
        text = f"{title} — jami {len(rows)} ta:\n\n" + "\n".join(
            f"{i}. <b>{date}</b>\n   {reason}" for i, (reason, date) in enumerate(rows, 1)
        )

    try:
        await callback.message.edit_text(text, reply_markup=MARKS_KB)
    except TelegramBadRequest:
        pass  # bir xil tugma ikki marta bosilsa "message is not modified" xatosi chiqadi
    await callback.answer()


# ==================== 6. ADMIN PANELI (tugmalar orqali) ====================
# Admin buyruq yodlamaydi: tugmani bosadi, bot kerakli ma'lumotni
# qadam-baqadam so'raydi. Har qadamda "❌ Bekor qilish" tugmasi bor.

class AddTeacher(StatesGroup):
    tg_id = State()
    first_name = State()
    last_name = State()
    sched_time = State()
    departure = State()


class ChangeTime(StatesGroup):
    sched_time = State()
    departure = State()


class CustomReport(StatesGroup):
    dates = State()


class GiveBonus(StatesGroup):
    reason = State()


class GiveJazo(StatesGroup):
    reason = State()


class EditSchedule(StatesGroup):
    picking = State()   # kunlarni belgilash
    arrive = State()    # kelish vaqtini kiritish
    leave = State()     # ketish vaqtini kiritish


@panel_router.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=menu_kb(message.from_user))


# ---------- O'qituvchi qo'shish (4 qadam) ----------

@panel_router.message(F.text == "➕ O'qituvchi qo'shish")
async def add_step_start(message: Message, state: FSMContext):
    await state.set_state(AddTeacher.tg_id)
    await message.answer(
        "<b>1/5-qadam:</b> O'qituvchining Telegram ID raqamini yuboring.\n\n"
        "💡 IDni bilish oson: o'qituvchi botga /start yozsa, bot unga ID raqamini "
        "ko'rsatadi — o'sha raqamni sizga yuborsin.\n"
        "Yoki o'qituvchidan kelgan istalgan xabarni shu yerga forward qiling.",
        reply_markup=CANCEL_KB,
    )


@panel_router.message(AddTeacher.tg_id)
async def add_step_id(message: Message, state: FSMContext):
    # Forward qilingan xabardan IDni avtomatik olamiz
    sender = getattr(message.forward_origin, "sender_user", None)
    if sender:
        tg_id = sender.id
    elif message.text and message.text.strip().isdigit():
        tg_id = int(message.text.strip())
    else:
        await message.answer(
            "ID butun son bo'lishi kerak, masalan: 123456789.\n"
            "Qaytadan yuboring yoki o'qituvchining xabarini forward qiling."
        )
        return

    existing = get_teacher(tg_id)
    if existing:
        await state.clear()
        await message.answer(
            f"⚠️ Bu ID allaqachon ro'yxatda: {existing[2]} {existing[3]}.",
            reply_markup=menu_kb(message.from_user),
        )
        return

    await state.update_data(tg_id=tg_id)
    await state.set_state(AddTeacher.first_name)
    await message.answer(f"ID qabul qilindi: <code>{tg_id}</code>\n\n<b>2/5-qadam:</b> Ismini yozing (masalan: Ali).")


@panel_router.message(AddTeacher.first_name, F.text)
async def add_step_first_name(message: Message, state: FSMContext):
    await state.update_data(first_name=message.text.strip())
    await state.set_state(AddTeacher.last_name)
    await message.answer("<b>3/5-qadam:</b> Familiyasini yozing (masalan: Valiyev).")


@panel_router.message(AddTeacher.last_name, F.text)
async def add_step_last_name(message: Message, state: FSMContext):
    await state.update_data(last_name=message.text.strip())
    await state.set_state(AddTeacher.sched_time)
    await message.answer("<b>4/5-qadam:</b> Ishga kelish vaqtini yozing (masalan: 09:00).")


@panel_router.message(AddTeacher.sched_time, F.text)
async def add_step_time(message: Message, state: FSMContext):
    sched_time = message.text.strip()
    if not TIME_RE.match(sched_time):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 09:00. Qaytadan yozing.")
        return

    await state.update_data(sched_time=sched_time)
    await state.set_state(AddTeacher.departure)
    await message.answer(
        "<b>5/5-qadam:</b> Markazdan ketish vaqtini yozing (masalan: 18:00).\n\n"
        f"💡 O'tkazib yuborish uchun <code>-</code> yuboring — standart {DEFAULT_DEPARTURE} qo'yiladi."
    )


@panel_router.message(AddTeacher.departure, F.text)
async def add_step_departure(message: Message, state: FSMContext):
    departure = message.text.strip()
    if departure == "-":
        departure = DEFAULT_DEPARTURE
    elif not TIME_RE.match(departure):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 18:00. Qaytadan yozing.")
        return

    data = await state.get_data()
    await state.clear()

    if add_teacher(data["tg_id"], data["first_name"], data["last_name"],
                   data["sched_time"], departure):
        await message.answer(
            f"✅ <b>{data['first_name']} {data['last_name']}</b> ro'yxatga qo'shildi!\n"
            f"🕘 Kelish vaqti: {data['sched_time']}\n"
            f"🕕 Ketish vaqti: {departure}\n\n"
            "Endi u botga /start yozib, \"✅ Keldim\" tugmasidan foydalana oladi.\n"
            "💡 Hafta kunlariga alohida vaqt kerak bo'lsa — \"📋 O'qituvchilar ro'yxati\" "
            "dan 🗓 tugmasini bosing.",
            reply_markup=menu_kb(message.from_user),
        )
    else:
        await message.answer(
            "⚠️ Bu ID bilan o'qituvchi allaqachon mavjud.",
            reply_markup=menu_kb(message.from_user),
        )


# ---------- O'qituvchilar ro'yxati (vaqt o'zgartirish / o'chirish) ----------

@panel_router.message(F.text == "📋 O'qituvchilar ro'yxati")
async def show_teachers_list(message: Message):
    teachers = db(
        "SELECT first_name, last_name, scheduled_time, departure_time, telegram_id FROM teachers "
        "ORDER BY first_name, last_name",
        fetch="all",
    )
    if not teachers:
        await message.answer(
            "Hozircha o'qituvchilar ro'yxati bo'sh.\n"
            "\"➕ O'qituvchi qo'shish\" tugmasi orqali birinchi o'qituvchini qo'shing."
        )
        return

    # Har bir o'qituvchi uchun: ⏰ — vaqt, 🗓 — haftalik jadval, 🗑 — o'chirish
    rows = []
    for first, last, sched, departure, tg_id in teachers:
        rows.append([InlineKeyboardButton(
            text=f"{first} {last} — {sched}/{departure}", callback_data=f"time:{tg_id}"
        )])
        rows.append([
            InlineKeyboardButton(text="⏰ Vaqt", callback_data=f"time:{tg_id}"),
            InlineKeyboardButton(text="🗓 Haftalik jadval", callback_data=f"sched:{tg_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"del:{tg_id}"),
        ])

    await message.answer(
        f"📋 O'qituvchilar ro'yxati ({len(teachers)} ta):\n"
        "Nom yonidagi raqamlar — kelish/ketish vaqti.\n\n"
        "⏰ — standart kelish va ketish vaqtini o'zgartirish\n"
        "🗓 — hafta kunlariga alohida vaqt belgilash\n"
        "🗑 — ro'yxatdan o'chirish",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@panel_router.callback_query(F.data.startswith("time:"))
async def change_time_start(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    teacher = get_teacher(tg_id)
    if not teacher:
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    await state.update_data(tg_id=tg_id)
    await state.set_state(ChangeTime.sched_time)
    await callback.message.answer(
        f"<b>{teacher[2]} {teacher[3]}</b> uchun yangi <b>kelish</b> vaqtini yozing "
        f"(hozirgisi: {teacher[4]}).\nMasalan: 09:30",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(ChangeTime.sched_time, F.text)
async def change_time_arrive(message: Message, state: FSMContext):
    sched_time = message.text.strip()
    if not TIME_RE.match(sched_time):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 09:30. Qaytadan yozing.")
        return

    data = await state.get_data()
    teacher = get_teacher(data["tg_id"])
    await state.update_data(sched_time=sched_time)
    await state.set_state(ChangeTime.departure)
    await message.answer(
        f"Endi <b>ketish</b> vaqtini yozing "
        f"(hozirgisi: {teacher[5] if teacher else DEFAULT_DEPARTURE}).\nMasalan: 18:00"
    )


@panel_router.message(ChangeTime.departure, F.text)
async def change_time_save(message: Message, state: FSMContext):
    departure = message.text.strip()
    if not TIME_RE.match(departure):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 18:00. Qaytadan yozing.")
        return

    data = await state.get_data()
    await state.clear()

    ok = db(
        "UPDATE teachers SET scheduled_time = ?, departure_time = ? WHERE telegram_id = ?",
        (data["sched_time"], departure, data["tg_id"]),
    ) > 0
    await message.answer(
        f"✅ Saqlandi.\n🕘 Kelish: {data['sched_time']}\n🕕 Ketish: {departure}"
        if ok else "⚠️ Bunday o'qituvchi topilmadi.",
        reply_markup=menu_kb(message.from_user),
    )


@panel_router.callback_query(F.data.startswith("del:"))
async def delete_confirm(callback: CallbackQuery):
    tg_id = int(callback.data.split(":")[1])
    teacher = get_teacher(tg_id)
    if not teacher:
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha, o'chirilsin", callback_data=f"delok:{tg_id}"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data="delno"),
    ]])
    await callback.message.answer(
        f"<b>{teacher[2]} {teacher[3]}</b> ro'yxatdan o'chirilsinmi?", reply_markup=kb
    )
    await callback.answer()


@panel_router.callback_query(F.data.startswith("delok:"))
async def delete_do(callback: CallbackQuery):
    tg_id = int(callback.data.split(":")[1])
    ok = db("DELETE FROM teachers WHERE telegram_id = ?", (tg_id,)) > 0
    await callback.message.edit_text(
        "✅ O'qituvchi ro'yxatdan o'chirildi." if ok else "⚠️ Bunday o'qituvchi topilmadi."
    )
    await callback.answer()


@panel_router.callback_query(F.data == "delno")
async def delete_cancel(callback: CallbackQuery):
    await callback.message.edit_text("Bekor qilindi.")
    await callback.answer()


# ---------- Haftalik jadval (kunlarni belgilab, vaqt qo'yish) ----------
# Masalan: dushanba/chorshanba/juma — 12:00, seshanba/payshanba/shanba — 13:00.
# Admin avval kunlarni belgilaydi, keyin o'sha kunlarga vaqt kiritadi.

def schedule_text(teacher, selected: set[int]) -> str:
    chosen = ", ".join(WEEKDAYS[weekday] for weekday in sorted(selected)) or "hech qaysi"
    return (
        f"🗓 <b>{teacher[2]} {teacher[3]}</b> — haftalik jadval\n\n"
        f"{format_week_schedule(teacher)}\n\n"
        f"<b>Belgilangan kunlar:</b> {chosen}\n\n"
        "Kunlarni bosib belgilang, so'ng pastdagi amallardan birini tanlang."
    )


async def show_schedule_editor(callback: CallbackQuery, state: FSMContext, edit: bool = True):
    data = await state.get_data()
    teacher = get_teacher(data["tg_id"])
    if not teacher:
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    selected = set(data.get("days", []))
    text, kb = schedule_text(teacher, selected), schedule_kb(selected)
    if edit:
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass  # matn o'zgarmagan bo'lsa Telegram xato qaytaradi
    else:
        await callback.message.answer(text, reply_markup=kb)


@panel_router.callback_query(F.data.startswith("sched:"))
async def schedule_start(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    if not get_teacher(tg_id):
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    await state.set_state(EditSchedule.picking)
    await state.update_data(tg_id=tg_id, days=[])
    await show_schedule_editor(callback, state, edit=False)
    await callback.answer()


@panel_router.callback_query(EditSchedule.picking, F.data.startswith("sday:"))
async def schedule_toggle_day(callback: CallbackQuery, state: FSMContext):
    weekday = int(callback.data.split(":")[1])
    data = await state.get_data()
    days = set(data.get("days", []))
    days.symmetric_difference_update({weekday})  # bosilgan kunni yoqadi/o'chiradi
    await state.update_data(days=sorted(days))
    await show_schedule_editor(callback, state)
    await callback.answer()


@panel_router.callback_query(EditSchedule.picking, F.data == "sset")
async def schedule_ask_arrive(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("days"):
        await callback.answer("Avval kamida bitta kunni belgilang.", show_alert=True)
        return

    days_text = ", ".join(WEEKDAYS[weekday] for weekday in data["days"])
    await state.set_state(EditSchedule.arrive)
    await callback.message.answer(
        f"<b>{days_text}</b> kunlari uchun <b>kelish</b> vaqtini yozing.\nMasalan: 12:00",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(EditSchedule.arrive, F.text)
async def schedule_save_arrive(message: Message, state: FSMContext):
    arrive = message.text.strip()
    if not TIME_RE.match(arrive):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 12:00. Qaytadan yozing.")
        return

    await state.update_data(arrive=arrive)
    await state.set_state(EditSchedule.leave)
    await message.answer(
        "Endi shu kunlar uchun <b>ketish</b> vaqtini yozing.\nMasalan: 18:00\n\n"
        "💡 O'tkazib yuborish uchun <code>-</code> yuboring."
    )


@panel_router.message(EditSchedule.leave, F.text)
async def schedule_save_leave(message: Message, state: FSMContext):
    leave = message.text.strip()
    if leave == "-":
        leave = None
    elif not TIME_RE.match(leave):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 18:00. Qaytadan yozing.")
        return

    data = await state.get_data()
    await state.clear()

    teacher = get_teacher(data["tg_id"])
    if not teacher:
        await message.answer("⚠️ Bu o'qituvchi topilmadi.", reply_markup=menu_kb(message.from_user))
        return

    for weekday in data["days"]:
        set_day_schedule(teacher[0], weekday, data["arrive"], leave)

    days_text = ", ".join(WEEKDAYS[weekday] for weekday in data["days"])
    await message.answer(
        f"✅ <b>{teacher[2]} {teacher[3]}</b> uchun saqlandi:\n"
        f"📅 {days_text}\n"
        f"🕘 Kelish: {data['arrive']}   🕕 Ketish: {leave or '—'}\n\n"
        f"<b>Yangi jadval:</b>\n{format_week_schedule(get_teacher(data['tg_id']))}",
        reply_markup=menu_kb(message.from_user),
    )


@panel_router.callback_query(EditSchedule.picking, F.data == "soff")
async def schedule_set_dayoff(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("days"):
        await callback.answer("Avval kamida bitta kunni belgilang.", show_alert=True)
        return

    teacher = get_teacher(data["tg_id"])
    if not teacher:
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    for weekday in data["days"]:
        set_day_schedule(teacher[0], weekday, None, None)

    await state.update_data(days=[])
    await show_schedule_editor(callback, state)
    await callback.answer("Dam olish kuni qilib belgilandi.")


@panel_router.callback_query(EditSchedule.picking, F.data == "sclear")
async def schedule_clear(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    teacher = get_teacher(data["tg_id"])
    if not teacher:
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    clear_week_schedule(teacher[0])
    await state.update_data(days=[])
    await show_schedule_editor(callback, state)
    await callback.answer("Jadval tozalandi — standart vaqt ishlatiladi.")


# ---------- Bonus va jazo berish ----------

async def notify_teacher(bot, tg_id: int, text: str) -> bool:
    """O'qituvchiga shaxsiy xabar yuboradi. U botni bloklagan bo'lsa False."""
    try:
        await bot.send_message(tg_id, text)
        return True
    except TelegramAPIError:
        logger.exception("O'qituvchiga (%s) xabar yuborib bo'lmadi", tg_id)
        return False


async def save_and_notify_mark(
    message: Message, admin: User, tg_id: int, mark_type: str, reason: str
):
    """Bonus/jazoni bazaga yozadi, o'qituvchini xabardor qiladi, adminga tasdiq beradi.
    `admin` alohida uzatiladi: callback ichidagi xabarning muallifi — botning o'zi."""
    teacher = get_teacher(tg_id)
    if not teacher:
        await message.answer("⚠️ Bu o'qituvchi topilmadi.", reply_markup=menu_kb(admin))
        return

    add_mark(teacher[0], mark_type, reason, admin.id)

    if mark_type == "bonus":
        note = (
            "🏅 <b>Sizga bonus berildi!</b>\n\n"
            f"📝 Sabab: {reason}\n"
            f"📅 Sana: {today()}\n\n"
            "Ajoyib ish, shunday davom eting! 🎉"
        )
    else:
        note = (
            "⚠️ <b>Sizga jazo berildi.</b>\n\n"
            f"📝 Sabab: {reason}\n"
            f"📅 Sana: {today()}\n\n"
            "Iltimos, bunday holat qaytarilmasligiga e'tibor bering."
        )

    delivered = await notify_teacher(message.bot, tg_id, note)
    label = "🏅 Bonus" if mark_type == "bonus" else "⚠️ Jazo"
    await message.answer(
        f"✅ {label} yozib qo'yildi.\n"
        f"👤 {teacher[2]} {teacher[3]}\n"
        f"📝 Sabab: {reason}\n\n"
        + ("📨 O'qituvchiga xabar yuborildi."
           if delivered else
           "⚠️ O'qituvchiga xabar yetkazilmadi (u botni bloklagan yoki /start bosmagan). "
           "Yozuv baribir saqlandi."),
        reply_markup=menu_kb(admin),
    )


@panel_router.message(F.text == "🏅 Bonus berish")
async def bonus_pick_teacher(message: Message, state: FSMContext):
    await state.clear()
    kb = teachers_pick_kb("bon")
    if kb is None:
        await message.answer("Avval o'qituvchi qo'shing.")
        return
    await message.answer("🏅 Kimga bonus bermoqchisiz?", reply_markup=kb)


@panel_router.callback_query(F.data.startswith("bon:"))
async def bonus_ask_reason(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    teacher = get_teacher(tg_id)
    if not teacher:
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    await state.set_state(GiveBonus.reason)
    await state.update_data(tg_id=tg_id)
    await callback.message.answer(
        f"🏅 <b>{teacher[2]} {teacher[3]}</b> uchun bonus sababini yozing.\n\n"
        "Masalan: <i>Oylik reja 120% bajarildi</i>",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(GiveBonus.reason, F.text)
async def bonus_save(message: Message, state: FSMContext):
    reason = message.text.strip()
    if len(reason) < 3:
        await message.answer("Sabab juda qisqa. Iltimos, batafsilroq yozing.")
        return

    data = await state.get_data()
    await state.clear()
    await save_and_notify_mark(message, message.from_user, data["tg_id"], "bonus", reason)


@panel_router.message(F.text == "⚠️ Jazo berish")
async def jazo_pick_teacher(message: Message, state: FSMContext):
    await state.clear()
    kb = teachers_pick_kb("jaz")
    if kb is None:
        await message.answer("Avval o'qituvchi qo'shing.")
        return
    await message.answer("⚠️ Kimga jazo bermoqchisiz?", reply_markup=kb)


@panel_router.callback_query(F.data.startswith("jaz:"))
async def jazo_pick_reason(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split(":")[1])
    teacher = get_teacher(tg_id)
    if not teacher:
        await callback.answer("Bu o'qituvchi topilmadi.", show_alert=True)
        return

    await state.update_data(tg_id=tg_id)
    rows = [
        [InlineKeyboardButton(text=reason, callback_data=f"jr:{tg_id}:{index}")]
        for index, reason in enumerate(JAZO_REASONS)
    ]
    rows.append([InlineKeyboardButton(text="✏️ Boshqa sabab", callback_data=f"jr:{tg_id}:x")])
    await callback.message.answer(
        f"⚠️ <b>{teacher[2]} {teacher[3]}</b> uchun jazo sababini tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@panel_router.callback_query(F.data.startswith("jr:"))
async def jazo_save(callback: CallbackQuery, state: FSMContext):
    _, tg_id_text, choice = callback.data.split(":")
    tg_id = int(tg_id_text)

    if choice == "x":
        await state.set_state(GiveJazo.reason)
        await state.update_data(tg_id=tg_id)
        await callback.message.answer("Jazo sababini yozing:", reply_markup=CANCEL_KB)
        await callback.answer()
        return

    await state.clear()
    await callback.answer()
    await save_and_notify_mark(
        callback.message, callback.from_user, tg_id, "jazo", JAZO_REASONS[int(choice)]
    )


@panel_router.message(GiveJazo.reason, F.text)
async def jazo_save_custom(message: Message, state: FSMContext):
    reason = message.text.strip()
    if len(reason) < 3:
        await message.answer("Sabab juda qisqa. Iltimos, batafsilroq yozing.")
        return

    data = await state.get_data()
    await state.clear()
    await save_and_notify_mark(message, message.from_user, data["tg_id"], "jazo", reason)


# ==================== 7. PDF HISOBOT ====================
# Hisobotda uchta jadval bo'ladi: umumiy yakun, kunlik davomat,
# hamda bonus/jazolar — har birining sababi bilan.

# Unicode shrift topilsa o'shani ishlatamiz (kirill harflar ham chiqadi),
# topilmasa PDF'ning o'zida bor Helvetica ishlatiladi.
FONT_CANDIDATES = [
    ("DejaVu",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("Arial",
     r"C:\Windows\Fonts\arial.ttf",
     r"C:\Windows\Fonts\arialbd.ttf"),
]

# Helvetica faqat latin-1 belgilarni biladi — tipografik belgilarni oddiysiga almashtiramiz
ASCII_MAP = str.maketrans({
    "ʻ": "'", "ʼ": "'", "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...",
})


def setup_pdf_font(pdf: FPDF) -> tuple[str, bool]:
    """(shrift nomi, unicode_mi) qaytaradi."""
    for name, regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            pdf.add_font(name, "", regular)
            pdf.add_font(name, "B", bold)
            return name, True
    return "Helvetica", False


def pdf_text(value, unicode_font: bool) -> str:
    """Matnni PDF shrifti qabul qiladigan ko'rinishga keltiradi."""
    text = str(value).translate(ASCII_MAP)
    if unicode_font:
        return text
    return text.encode("latin-1", "replace").decode("latin-1")


def build_report_pdf(records, marks, date_from, date_to) -> bytes:
    """Davomat va bonus/jazo ma'lumotlaridan PDF yasaydi."""
    pdf = FPDF(orientation="P", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    font, unicode_font = setup_pdf_font(pdf)
    pdf.add_page()

    def safe(value):
        return pdf_text(value, unicode_font)

    def table(headers, rows, widths, aligns):
        with pdf.table(
            col_widths=widths,
            text_align=aligns,
            headings_style=FontFace(emphasis="BOLD", color=(255, 255, 255),
                                    fill_color=(68, 114, 196)),
            line_height=6,
            padding=1.5,
        ) as tbl:
            head = tbl.row()
            for header in headers:
                head.cell(safe(header))
            for data_row in rows:
                row = tbl.row()
                for value in data_row:
                    row.cell(safe(value))

    # ---- Sarlavha ----
    pdf.set_font(font, "B", 16)
    pdf.cell(0, 10, safe("Davomat hisoboti"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font(font, "", 10)
    pdf.cell(0, 6, safe(f"Davr: {date_from.isoformat()} - {date_to.isoformat()}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.cell(0, 6, safe(f"Tayyorlandi: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(4)

    # ---- 1-jadval: har bir o'qituvchi bo'yicha yakun ----
    def new_item():
        return {"days": 0, "late": 0, "minutes": 0, "fine": 0, "bonus": 0, "jazo": 0}

    summary: dict[str, dict] = {}
    for first, last, _date, _arrived, _sched, is_late, late_min, _early, fine in records:
        item = summary.setdefault(f"{first} {last}", new_item())
        item["days"] += 1
        item["fine"] += fine
        if is_late:
            item["late"] += 1
            item["minutes"] += late_min
    for first, last, mark_type, _reason, _date in marks:
        item = summary.setdefault(f"{first} {last}", new_item())
        item[mark_type] += 1

    total_fine_all = sum(item["fine"] for item in summary.values())

    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, safe("1. Umumiy yakun"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 9)
    table(
        ["O'qituvchi", "Kelgan", "Kech kun", "Jami jarima", "Bonus", "Jazo"],
        [
            [name, item["days"], item["late"],
             format_money(item["fine"]) if item["fine"] else "-",
             item["bonus"], item["jazo"]]
            for name, item in sorted(summary.items())
        ],
        widths=(52, 22, 24, 46, 18, 18),
        aligns=("LEFT", "CENTER", "CENTER", "RIGHT", "CENTER", "CENTER"),
    )
    pdf.ln(2)
    pdf.set_font(font, "B", 10)
    pdf.cell(0, 7, safe(f"Barcha o'qituvchilar bo'yicha jami jarima: {format_money(total_fine_all)}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # ---- 2-jadval: kunlik davomat ----
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, safe("2. Kunlik davomat"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 8)
    pdf.cell(0, 5, safe(
        f"Erta kelish oynasi: har daqiqa {format_money(FINE_EARLY_PER_MINUTE)}  |  "
        f"Belgilangan vaqtdan keyin: har daqiqa {format_money(FINE_LATE_PER_MINUTE)}"),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 9)
    if records:
        table(
            ["O'qituvchi", "Sana", "Kelgan", "Belg.", "Erta daq.", "Kech daq.", "Jarima"],
            [
                [f"{first} {last}", date, arrived, sched or "-",
                 str(early) if early else "-",
                 str(late_min) if late_min else "-",
                 format_money(fine) if fine else "-"]
                for first, last, date, arrived, sched, is_late, late_min, early, fine in records
            ],
            widths=(42, 24, 22, 18, 20, 20, 34),
            aligns=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER", "RIGHT"),
        )
    else:
        pdf.cell(0, 6, safe("Bu davrda davomat yozuvi yo'q."),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    # ---- 3-jadval: bonus va jazolar, sabablari bilan ----
    pdf.set_font(font, "B", 12)
    pdf.cell(0, 8, safe("3. Bonus va jazolar"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(font, "", 9)
    if marks:
        table(
            ["O'qituvchi", "Sana", "Turi", "Sababi"],
            [
                [f"{first} {last}", date,
                 "BONUS" if mark_type == "bonus" else "JAZO", reason]
                for first, last, mark_type, reason, date in marks
            ],
            widths=(42, 22, 20, 96),
            aligns=("LEFT", "CENTER", "CENTER", "LEFT"),
        )
    else:
        pdf.cell(0, 6, safe("Bu davrda bonus yoki jazo berilmagan."),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


async def send_pdf_report(message: Message, date_from, date_to) -> None:
    """Berilgan davr uchun PDF hisobot tayyorlab yuboradi."""
    records = db(
        """
        SELECT t.first_name, t.last_name, a.attendance_date, a.arrived_time,
               COALESCE(a.scheduled_time, t.scheduled_time), a.is_late, a.late_minutes,
               a.early_minutes, a.fine_amount
        FROM attendance a
        JOIN teachers t ON t.id = a.teacher_id
        WHERE a.attendance_date BETWEEN ? AND ?
        ORDER BY a.attendance_date, t.first_name, t.last_name
        """,
        (date_from.isoformat(), date_to.isoformat()), fetch="all",
    )
    marks = db(
        """
        SELECT t.first_name, t.last_name, m.mark_type, m.reason, m.mark_date
        FROM marks m
        JOIN teachers t ON t.id = m.teacher_id
        WHERE m.mark_date BETWEEN ? AND ?
        ORDER BY m.mark_date, t.first_name, t.last_name
        """,
        (date_from.isoformat(), date_to.isoformat()), fetch="all",
    )

    if not records and not marks:
        await message.answer(
            f"{date_from.isoformat()} — {date_to.isoformat()} oralig'ida "
            "davomat, bonus yoki jazo yozuvi topilmadi."
        )
        return

    pdf_bytes = build_report_pdf(records, marks, date_from, date_to)
    bonus_count = sum(1 for _, _, mark_type, _, _ in marks if mark_type == "bonus")
    total_fine = sum(row[8] for row in records)

    await message.answer_document(
        BufferedInputFile(
            pdf_bytes,
            filename=f"davomat_{date_from.isoformat()}_{date_to.isoformat()}.pdf",
        ),
        caption=(
            f"📄 Davomat hisoboti\n"
            f"Davr: {date_from.isoformat()} — {date_to.isoformat()}\n"
            f"📊 Davomat yozuvlari: {len(records)}\n"
            f"💰 Jami jarima: {format_money(total_fine)}\n"
            f"🏅 Bonuslar: {bonus_count}   ⚠️ Jazolar: {len(marks) - bonus_count}"
        ),
    )


@panel_router.message(F.text == "📄 PDF hisobot")
async def report_menu(message: Message):
    await message.answer("Qaysi davr uchun hisobot kerak?", reply_markup=REPORT_KB)


@panel_router.callback_query(F.data.in_({"rep_this", "rep_prev"}))
async def report_quick(callback: CallbackQuery):
    today_date = datetime.now(TZ).date()
    if callback.data == "rep_this":
        date_from, date_to = today_date.replace(day=1), today_date
    else:  # o'tgan oy
        date_to = today_date.replace(day=1) - timedelta(days=1)  # o'tgan oyning oxiri
        date_from = date_to.replace(day=1)

    await callback.answer()
    await send_pdf_report(callback.message, date_from, date_to)


@panel_router.callback_query(F.data == "rep_custom")
async def report_custom_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CustomReport.dates)
    await callback.message.answer(
        "Boshlanish va tugash sanalarini bitta xabarda yuboring.\n"
        "Namuna: <code>2026-07-01 2026-07-16</code>",
        reply_markup=CANCEL_KB,
    )
    await callback.answer()


@panel_router.message(CustomReport.dates, F.text)
async def report_custom_dates(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) != 2 or not (DATE_RE.match(parts[0]) and DATE_RE.match(parts[1])):
        await message.answer(
            "Ikkita sanani YYYY-MM-DD formatida yuboring.\n"
            "Namuna: <code>2026-07-01 2026-07-16</code>"
        )
        return

    try:
        date_from = datetime.strptime(parts[0], "%Y-%m-%d").date()
        date_to = datetime.strptime(parts[1], "%Y-%m-%d").date()
    except ValueError:
        await message.answer("Sana formati noto'g'ri. Namuna: 2026-07-01")
        return

    if date_from > date_to:
        await message.answer("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas.")
        return

    await state.clear()
    await message.answer("⏳ Hisobot tayyorlanmoqda...", reply_markup=menu_kb(message.from_user))
    await send_pdf_report(message, date_from, date_to)


# ==================== 8. ADMIN BUYRUQLARI (eski, ixtiyoriy) ====================
# Tugmalar o'rniga matnli buyruqlarni yoqtirganlar uchun saqlab qolingan.

@admin_router.message(Command("add_teacher"))
async def cmd_add_teacher(message: Message):
    if not is_admin(message.from_user):
        return

    # Format: /add_teacher <telegram_id> <Ism> <Familiya> <HH:MM>
    parts = message.text.split(maxsplit=4)
    if len(parts) != 5:
        await message.answer(
            "Foydalanish: /add_teacher <telegram_id> <Ism> <Familiya> <HH:MM>\n"
            "Masalan: /add_teacher 123456789 Ali Valiyev 09:00\n\n"
            "💡 Osonroq yo'l: \"➕ O'qituvchi qo'shish\" tugmasini bosing."
        )
        return

    _, tg_id, first_name, last_name, sched_time = parts
    if not tg_id.isdigit():
        await message.answer("telegram_id butun son bo'lishi kerak.")
        return
    if not TIME_RE.match(sched_time):
        await message.answer("Vaqt HH:MM formatida bo'lishi kerak, masalan: 09:00")
        return

    if add_teacher(int(tg_id), first_name, last_name, sched_time):
        await message.answer(
            f"✅ Qo'shildi: {first_name} {last_name} (belgilangan vaqt: {sched_time})"
        )
    else:
        await message.answer("⚠️ Bu telegram_id bilan o'qituvchi allaqachon mavjud.")


@admin_router.message(Command("remove_teacher"))
async def cmd_remove_teacher(message: Message):
    if not is_admin(message.from_user):
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /remove_teacher <telegram_id>")
        return

    ok = db("DELETE FROM teachers WHERE telegram_id = ?", (int(parts[1]),)) > 0
    await message.answer("✅ O'chirildi." if ok else "⚠️ Bunday o'qituvchi topilmadi.")


@admin_router.message(Command("set_time"))
async def cmd_set_time(message: Message):
    if not is_admin(message.from_user):
        return

    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not TIME_RE.match(parts[2]):
        await message.answer(
            "Foydalanish: /set_time <telegram_id> <HH:MM>\n"
            "Masalan: /set_time 123456789 09:30"
        )
        return

    ok = db(
        "UPDATE teachers SET scheduled_time = ? WHERE telegram_id = ?",
        (parts[2], int(parts[1])),
    ) > 0
    await message.answer(f"✅ Yangilandi: {parts[2]}" if ok else "⚠️ Bunday o'qituvchi topilmadi.")


@admin_router.message(Command("list_teachers"))
async def cmd_list_teachers(message: Message):
    if not is_admin(message.from_user):
        return

    teachers = db(
        "SELECT first_name, last_name, scheduled_time, departure_time, telegram_id "
        "FROM teachers", fetch="all",
    )
    if not teachers:
        await message.answer("Hozircha o'qituvchilar ro'yxati bo'sh.")
        return

    lines = ["📋 O'qituvchilar ro'yxati:\n"] + [
        f"• {first} {last} — {sched} dan {departure} gacha (ID: {tg_id})"
        for first, last, sched, departure, tg_id in teachers
    ]
    await message.answer("\n".join(lines))


@admin_router.message(Command("pdf_hisobot"))
async def cmd_pdf_report(message: Message):
    if not is_admin(message.from_user):
        return

    parts = message.text.split()
    if len(parts) == 1:
        # Argument berilmasa — shu oy uchun hisobot
        today_date = datetime.now(TZ).date()
        date_from, date_to = today_date.replace(day=1), today_date
    elif len(parts) == 3 and DATE_RE.match(parts[1]) and DATE_RE.match(parts[2]):
        try:
            date_from = datetime.strptime(parts[1], "%Y-%m-%d").date()
            date_to = datetime.strptime(parts[2], "%Y-%m-%d").date()
        except ValueError:
            await message.answer("Sana formati noto'g'ri. Namuna: 2026-07-01")
            return
    else:
        await message.answer(
            "Foydalanish:\n"
            "/pdf_hisobot — shu oy uchun hisobot\n"
            "/pdf_hisobot 2026-07-01 2026-07-16 — belgilangan sanalar oralig'i uchun\n\n"
            "💡 Osonroq yo'l: \"📄 PDF hisobot\" tugmasini bosing."
        )
        return

    if date_from > date_to:
        await message.answer("Boshlanish sanasi tugash sanasidan katta bo'lishi mumkin emas.")
        return

    await send_pdf_report(message, date_from, date_to)


# ==================== 9. ISHGA TUSHIRISH ====================

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(admin_router, panel_router, teacher_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
