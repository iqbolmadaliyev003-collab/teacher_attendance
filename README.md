# O'quv markaz — O'qituvchilar davomat boti

O'qituvchilarning markazga qancha vaqt kech kelganini lokatsiya orqali tekshiradigan Telegram bot.

## Qanday ishlaydi

1. O'qituvchi botga `/start` yozadi. Agar u ro'yxatda bo'lsa, "✅ Keldim" tugmasi chiqadi. Ro'yxatda bo'lmasa, bot unga o'z Telegram ID raqamini ko'rsatadi — u shu raqamni adminga yuboradi va admin uni ro'yxatga qo'shadi.
2. O'qituvchi markazga kelib, "✅ Keldim" tugmasini bosadi.
3. Bot lokatsiya so'raydi.
4. O'qituvchi lokatsiyasini yuboradi:
   - Agar lokatsiya markazdan `RADIUS_METERS` dan uzoqroq bo'lsa → bot "Siz hali markazga yetib kelmagansiz" deb javob beradi, guruhga hech narsa yubormaydi.
   - Agar lokatsiya mos tushsa → guruhga ism-familiya va real vaqt bilan xabar yuboriladi. Agar belgilangan vaqtdan kech bo'lsa, necha daqiqa/soat kechikkani ham yoziladi.
5. Har bir o'qituvchi kuniga faqat bitta marta "keldim" deb belgilashi mumkin.
6. O'qituvchi istalgan vaqtda "📊 Statistikam" tugmasini bosib, **Bugun / Bu hafta / Bu oy** bo'yicha nechta kun kelgani, nechta marta va jami necha daqiqa/soat kech qolganini ko'rishi mumkin (masalan: "6 marta, jami 30 daqiqa kech qoldingiz").
7. "🏅 Bonus va jazolarim" tugmasi orqali o'qituvchi o'ziga nechta bonus va nechta jazo berilganini, har birining sababi va sanasi bilan ko'radi.

## Haftalik jadval

Har bir o'qituvchiga hafta kunlari bo'yicha alohida kelish va ketish vaqti belgilanishi mumkin — masalan dushanba/chorshanba/juma soat 12:00, seshanba/payshanba/shanba soat 13:00, yakshanba esa dam olish kuni.

Kechikish o'sha kunga belgilangan vaqt bo'yicha hisoblanadi. Kun uchun alohida vaqt belgilanmagan bo'lsa, o'qituvchining standart vaqti ishlatiladi. Dam olish kuni deb belgilangan kunlarda kechikish umuman hisoblanmaydi.

## Jarima (kechikish uchun)

Ustoz belgilangan vaqtdan **5 daqiqa oldin** kelishi kerak. Masalan belgilangan vaqt 09:00 bo'lsa, ustoz 08:55 gacha kelishi shart.

- **08:55 gacha** kelsa — jarima yo'q.
- **08:55 dan 09:00 gacha** kechikkan har bir daqiqa — **5 000 so'm** (masalan 08:58 da kelsa, 3 daqiqa × 5 000 = 15 000 so'm).
- **09:00 dan keyin** kechikkan har bir daqiqa — **7 000 so'm** (bunda avvalgi 5 daqiqalik oyna to'liq, ya'ni 25 000 so'm, ustiga qo'shiladi).

Har bir "keldim"da jarima avtomatik hisoblanadi, guruhga va ustozga xabar sifatida boradi hamda PDF hisobotga tushadi. Dam olish kunlarida jarima hisoblanmaydi.

Bu qiymatlar (`5 daqiqa`, `5 000`, `7 000`) `config.py` faylida `EARLY_REQUIRED_MINUTES`, `FINE_EARLY_PER_MINUTE`, `FINE_LATE_PER_MINUTE` orqali o'zgartiriladi.

## Bonus va jazolar

Admin istalgan o'qituvchiga bonus yoki jazo yozib qo'yishi mumkin — o'qituvchiga darhol shaxsiy xabar boradi va yozuv hisobotga tushadi.

- **Bonus** — sababini admin o'zi yozadi.
- **Jazo** — sabab tayyor ro'yxatdan tanlanadi:
  1. Rangli ichimlik yoki xidli mahsulot iste'mol qilish
  2. Uniforma kiymaganligi
  3. Ish vaqtida mobil qurilmalardan foydalanish

  Kerak bo'lsa "✏️ Boshqa sabab" orqali o'z matnini ham yozish mumkin.

## O'rnatish

```bash
pip install -r requirements.txt
```

`.env` fayl kerak emas — barcha sozlamalar `config.py` faylida oddiy Python o'zgaruvchilari sifatida turadi. Botni ishga tushirishdan oldin `config.py` faylini ochib, quyidagilarni o'zingizga moslab to'g'ridan-to'g'ri o'zgartiring:

- `BOT_TOKEN` — @BotFather'dan olingan token
- `GROUP_CHAT_ID` — xabarlar yuboriladigan guruh IDsi (botni guruhga admin qilib qo'shing)
- `ADMIN_IDS` — o'qituvchi qo'shish huquqiga ega shaxslarning Telegram ID raqamlari (masalan: `{123456789, 987654321}`)
- `CENTER_LATITUDE`, `CENTER_LONGITUDE` — o'quv markazning lokatsiyasi
- `RADIUS_METERS` — ruxsat etilgan masofa (metr)

## Admin paneli (tugmalar orqali)

Admin botga `/start` yozsa, boshqaruv tugmalari chiqadi — hech qanday buyruq yodlash shart emas:

- **➕ O'qituvchi qo'shish** — bot 5 qadamda hamma narsani o'zi so'raydi: ID, ism, familiya, kelish vaqti, ketish vaqti. O'qituvchining xabarini forward qilsangiz, ID avtomatik olinadi.
- **📋 O'qituvchilar ro'yxati** — har bir o'qituvchi yonida ⏰ (kelish/ketish vaqtini o'zgartirish), 🗓 (haftalik jadval) va 🗑 (o'chirish, tasdiq bilan) tugmalari bo'ladi.
- **🏅 Bonus berish** — o'qituvchini tanlaysiz, sababini yozasiz, bot o'qituvchiga xabar yuboradi.
- **⚠️ Jazo berish** — o'qituvchini tanlaysiz, sababini ro'yxatdan tanlaysiz.
- **📄 PDF hisobot** — "Shu oy", "O'tgan oy" yoki istalgan davr uchun hisobotni bir bosishda yuklab olish.

**🗓 Haftalik jadval qanday belgilanadi:** kerakli kunlarni bosib belgilaysiz (☑️), so'ng "⏰ Tanlangan kunlarga vaqt belgilash" tugmasini bosib kelish va ketish vaqtini kiritasiz. "🌙 Tanlangan kunlar — dam olish" belgilangan kunlarni dam olish kuniga aylantiradi, "🗑 Jadvalni tozalash" esa standart vaqtga qaytaradi.

Har qadamda "❌ Bekor qilish" tugmasi bor.

**O'qituvchining ID raqamini qanday bilish mumkin?** O'qituvchi botga `/start` yozsa, bot unga o'z ID raqamini ko'rsatadi — o'sha raqamni adminga yuborsa kifoya.

## Admin buyruqlari (ixtiyoriy, eski usul)

Tugmalar o'rniga matnli buyruqlardan ham foydalansa bo'ladi:

- `/add_teacher <telegram_id> <Ism> <Familiya> <HH:MM>` — yangi o'qituvchi qo'shish
  masalan: `/add_teacher 123456789 Ali Valiyev 09:00`
- `/set_time <telegram_id> <HH:MM>` — o'qituvchining belgilangan vaqtini o'zgartirish
- `/remove_teacher <telegram_id>` — o'qituvchini ro'yxatdan o'chirish
- `/list_teachers` — barcha o'qituvchilar ro'yxati
- `/pdf_hisobot` — shu oy uchun barcha o'qituvchilarning hisobotini PDF fayl qilib yuboradi
- `/pdf_hisobot 2026-07-01 2026-07-16` — belgilangan sanalar oralig'i uchun hisobot

## PDF hisobot ichida nima bo'ladi

1. **Umumiy yakun** — har bir o'qituvchi bo'yicha: kelgan kunlar, kech qolgan kunlar, **jami jarima**, bonus va jazolar soni. Pastida barcha o'qituvchilar bo'yicha umumiy jarima yig'indisi.
2. **Kunlik davomat** — sana, kelgan vaqt, belgilangan vaqt, erta oynadagi daqiqalar (5 000 so'm/daqiqa), kech qolgan daqiqalar (7 000 so'm/daqiqa) va o'sha kungi **jarima summasi**.
3. **Bonus va jazolar** — kimga, qachon, qaysi turi va **sababi** bilan.

## Ishga tushirish

```bash
python main.py
```

## Railway'ga deploy qilish

1. `config.py` faylida barcha qiymatlarni to'ldiring (BOT_TOKEN, GROUP_CHAT_ID va h.k.).
2. Loyihani GitHub'ga yuklang.
3. Railway'da yangi loyiha yarating va GitHub repo'ni ulang.
4. `Procfile` avtomatik ravishda `worker: python main.py` jarayonini ishga tushiradi.
5. Muhim: `config.py` ichidagi maxfiy ma'lumotlar (BOT_TOKEN) GitHub'da ochiq repo bo'lsa ko'rinib qoladi — agar repo public bo'lsa, uni private qiling.
5. Diqqat: SQLite fayli (`attendance.db`) konteyner qayta ishga tushganda o'chib ketishi mumkin — agar davomat tarixini doimiy saqlamoqchi bo'lsangiz, Railway'ning Postgres qo'shimchasidan foydalanib, `main.py`dagi ma'lumotlar bazasi qismini PostgreSQL'ga moslashtirish tavsiya etiladi.

## Fayl tuzilishi

```
attendance_bot/
├── main.py            # Botning barcha kodi (baza, handlerlar, klaviaturalar)
├── config.py          # Barcha sozlamalar shu yerda (BOT_TOKEN va h.k.)
├── requirements.txt
└── Procfile           # Railway uchun
```
"# teacher_attendance" 
