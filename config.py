# =========================================================
#  BOT SOZLAMALARI
#
#  Maxfiy qiymat (BOT_TOKEN) environment variable orqali olinadi —
#  u GitHub'ga tushmaydi. Qolgan sozlamalarni shu yerda
#  to'g'ridan-to'g'ri o'zgartirsangiz bo'ladi.
#
#  LOKAL ISHLATISH: shu papkada ".env" fayl yarating va ichiga yozing:
#      BOT_TOKEN=123456789:ABCdef...  (o'z tokeningiz)
#
#  RAILWAY'DA: loyihaning "Variables" bo'limiga BOT_TOKEN ni qo'shing.
# =========================================================

import os

# Lokal ishlatishda .env faylini o'qiydi. Railway'da .env bo'lmaydi —
# u yerda qiymatlar to'g'ridan-to'g'ri environment'da turadi.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv o'rnatilmagan bo'lsa ham env o'qiladi


# @BotFather'dan olingan bot tokeni — environment variable'dan olinadi
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi!\n"
        "  • Railway'da: loyiha → Variables → BOT_TOKEN qo'shing.\n"
        "  • Lokal: shu papkada .env fayl yarating va 'BOT_TOKEN=...' yozing."
    )

# Xabarlar yuboriladigan guruh IDsi (odatda manfiy son, masalan -1001234567890)
# Guruh IDsini @getidsbot yordamida bilib olishingiz mumkin.
# Xohlasangiz, Railway'da GROUP_CHAT_ID nomli variable orqali ham o'zgartirasiz.
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "-5489791329"))

# O'qituvchi qo'shish huquqiga ega adminlarning Telegram ID raqamlari.
# Bir nechta bo'lsa vergul yoki bo'sh joy bilan yozing: "123,456"
# O'z IDingizni @userinfobot orqali bilib olasiz.
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "625900787").replace(",", " ").split()
}

# O'quv markazning markaziy nuqtasi (lokatsiyasi) — BOSHLANG'ICH qiymat.
# Botda admin "📍 Markaz joylashuvi" tugmasi orqali buni istalgan vaqtda
# o'zgartira oladi (o'quv markaz ichida turib jonli joylashuv yuborsa yetarli).
# O'zgartirilgan qiymat bazada saqlanadi va shu yerdagi sondan ustun turadi.
CENTER_LATITUDE = 40.546782
CENTER_LONGITUDE = 70.968727

# Ruxsat etilgan radius (metrlarda) — shu radius ichida bo'lsa "keldi" deb hisoblanadi.
# Bino ichida GPS signali zaif bo'ladi, telefon Wi-Fi/uyali tarmoq bo'yicha
# joylashuvni taxminlaydi — shuning uchun 50 metr juda tor. 100 metr xavfsizroq.
# Buni ham admin botdan turib o'zgartira oladi.
RADIUS_METERS = 100

# GPS xatoligiga beriladigan qo'shimcha yon berish (metrlarda).
# Telegram har bir joylashuv bilan birga "horizontal_accuracy" — ya'ni
# "joylashuv shuncha metr xato bo'lishi mumkin" degan qiymatni yuboradi.
# Bino ichida telefon sun'iy yo'ldoshni ko'rmaydi va joylashuvni Wi-Fi/uyali
# tarmoq bo'yicha taxminlaydi — xatolik 100-300 metrgacha chiqadi. Shu xatolikni
# hisobga olmasak, markazda o'tirgan ustoz ham "uzoqdasiz" javobini oladi.
#
# Shuning uchun masofadan telefon aytgan xatolik ayiriladi, lekin ko'pi bilan
# quyidagi qiymatgacha. Chegara kerak: aks holda soxta GPS dasturi "mening
# xatoligim 10 000 metr" deb yozib, uzoqdan ham "keldim" qilishi mumkin.
#
# Qiymatni pasaytirsangiz — qattiqroq nazorat, lekin noto'g'ri rad etish ko'payadi.
# Ko'tarsangiz — aksincha.
GPS_ACCURACY_TOLERANCE_METERS = 300

# ============ JARIMA (kechikish uchun) ============
# Ustoz belgilangan vaqtdan shuncha daqiqa OLDIN kelishi kerak.
# Masalan belgilangan vaqt 09:00 bo'lsa, ustoz 08:55 gacha kelishi shart.
EARLY_REQUIRED_MINUTES = 5

# "Erta kelish" oynasida — ya'ni deadline (08:55) bilan belgilangan vaqt (09:00)
# orasida — kechiktirilgan har bir daqiqa uchun jarima (so'mda).
FINE_EARLY_PER_MINUTE = 5000

# Belgilangan vaqtdan (09:00) keyin kechikkan har bir daqiqa uchun jarima (so'mda).
FINE_LATE_PER_MINUTE = 7000

# Belgilangan vaqtdan keyin jarima eng ko'pi shu daqiqagacha hisoblanadi.
# Undan ham ko'proq kech qolsa — hisoblash to'xtaydi va ustozga ogohlantirish yuboriladi.
LATE_FINE_CAP_MINUTES = 10

# Necha marta "keragidan ortiq kechikish" (ogohlantirish) bo'lsa qattiq chora ko'riladi.
WARNING_STRIKE_LIMIT = 3

# Vaqt zonasi
TIMEZONE = "Asia/Tashkent"

# Ma'lumotlar bazasi fayli
DB_PATH = "attendance.db"
