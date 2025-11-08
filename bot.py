# bot.py
import os
import json
import time
import socket
from math import pi
from datetime import datetime

import telebot
from telebot import types, apihelper
from telebot.apihelper import ApiTelegramException
from requests.exceptions import ReadTimeout, ConnectTimeout

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import arabic_reshaper
from bidi.algorithm import get_display

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader


# =========================
# تنظیمات
# =========================
BOT_TOKEN = "8304926524:AAFG9fp-KJik4sDVmvTXmojC8JQkDSkzs-0"
CHANNEL_USERNAME = "@thisistraderssupp"   # مثال: "@mytradingchannel"

# چند ادمین (آیدی عددی تلگرام)
ADMINS = [841704729, 7188957574]                  # هر تعداد خواستی اضافه کن

apihelper.CONNECT_TIMEOUT = 30
apihelper.READ_TIMEOUT = 120

bot = telebot.TeleBot(BOT_TOKEN)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGE1_FILE = os.path.join(BASE_DIR, "stage1.json")
STAGE2_FILE = os.path.join(BASE_DIR, "stage2.json")
DIMENSIONS_FILE = os.path.join(BASE_DIR, "dimensions.json")
PROFILES_FILE = os.path.join(BASE_DIR, "profiles.json")
REPORT_TEMPLATE_FILE = os.path.join(BASE_DIR, "report_template.json")

RESULTS_FILE = os.path.join(BASE_DIR, "results.json")
FONT_PATH = os.path.join(BASE_DIR, "Vazir.ttf")

REPORTS_DIR = os.path.join(BASE_DIR, "reports")
CHARTS_DIR  = os.path.join(BASE_DIR, "charts")
AUDIO_DIR   = os.path.join(BASE_DIR, "audio")

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(CHARTS_DIR,  exist_ok=True)

VOICE_DELAY_SECONDS = 1.3  # فاصله بین ویس‌های مرحله۱

if "Vazir" not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont("Vazir", FONT_PATH))


# =========================
# Utils برای ادمین‌ها
# =========================
def notify_admins_text(text: str):
    for admin_id in ADMINS:
        try:
            bot.send_message(admin_id, text)
            time.sleep(0.2)
        except Exception as e:
            print("DEBUG notify_admins_text:", admin_id, e)

def notify_admins_document(file_path: str, caption: str = ""):
    for admin_id in ADMINS:
        try:
            with open(file_path, "rb") as f:
                bot.send_document(admin_id, f, caption=caption, timeout=120)
            time.sleep(0.2)
        except Exception as e:
            print("DEBUG notify_admins_document:", admin_id, e)

import json, os, re

RESULTS_FILE = os.path.join(BASE_DIR, "results.json")  # اگر متنی داری، بر همان اساس تغییر بده

def _load_results_list():
    if not os.path.exists(RESULTS_FILE):
        return []
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_results_list(rows):
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def allow_retake_for(uid: int, hard_delete: bool = True):
    """اجازه‌ی تکرار تست برای یک کاربر: پاک‌کردن state حافظه + تمیزکاری فایل نتایج."""
    # 1) پاک‌کردن state در حافظه
    if uid in user_states:
        del user_states[uid]  # هر فلگ finished_stage* و ... حذف می‌شود

    # 2) ویرایش فایل نتایج
    rows = _load_results_list()
    if hard_delete:
        rows = [r for r in rows if str(r.get("telegram_id")) != str(uid)]
    else:
        # اگر نخوای رکورد حذف شود، فقط completed را False کن
        for r in rows:
            if str(r.get("telegram_id")) == str(uid):
                r["completed"] = False
    _save_results_list(rows)

    # 3) (اختیاری) پاک‌کردن فایل‌های PDF و نمودارهای قبلی این کاربر
    try:
        # reports/report_<uid>_*.pdf
        if os.path.isdir(REPORTS_DIR):
            for name in os.listdir(REPORTS_DIR):
                if re.match(rf"report_{uid}_\d+\.pdf$", name):
                    try: os.remove(os.path.join(REPORTS_DIR, name))
                    except: pass
        # charts/radar_<uid>.png, charts/bar_<uid>.png
        for p in [os.path.join(CHARTS_DIR, f"radar_{uid}.png"),
                  os.path.join(CHARTS_DIR, f"bar_{uid}.png")]:
            if os.path.exists(p):
                try: os.remove(p)
                except: pass
    except Exception as e:
        print("DEBUG cleanup files:", e)

@bot.message_handler(commands=["allow_retake"])
def on_allow_retake(msg):
    # فقط ادمین‌ها
    if msg.from_user.id not in ADMINS:
        return

    # فرمت: /allow_retake 123456789  (آیدی تلگرام کاربر)
    pieces = msg.text.strip().split()
    if len(pieces) != 2 or not pieces[1].isdigit():
        bot.reply_to(msg, "فرمت درست:\n/allow_retake <telegram_id>\nمثال:\n/allow_retake 123456789")
        return

    target_uid = int(pieces[1])
    allow_retake_for(target_uid, hard_delete=True)  # یا False اگر نمی‌خواهی رکورد حذف شود
    bot.reply_to(msg, f"✅ اجازه‌ی تکرار برای کاربر {target_uid} صادر شد.\nاو می‌تواند /start را بزند و دوباره تست بدهد.")

# =========================
# State
# =========================
user_states = {}

def init_user_state(uid):
    user_states[uid] = {
        "stage": None,
        "current_q_index": 0,
        "answers_stage1": {},
        "answers_stage2": {},
        "finished_stage1": False,
        "finished_stage2": False,
        "stage1_report": {},
        "stage2_report": {},
        "last_message_id": None,
        "mentoring_requested": False,
        "report_ready": False,
        "report_sent": False
    }

def get_user_state(uid):
    if uid not in user_states:
        init_user_state(uid)
    return user_states[uid]


# =========================
# IO + نرمال‌سازی سوال‌ها
# =========================
def _normalize_questions(stage_data: dict) -> dict:
    """id سوال‌ها را تا حد امکان به int تبدیل می‌کند تا کلیدها یکسان شوند."""
    for q in stage_data.get("questions", []):
        try:
            q["id"] = int(q["id"])
        except Exception:
            pass
    return stage_data

def load_stage(stage_id):
    path = STAGE1_FILE if stage_id == 1 else STAGE2_FILE
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _normalize_questions(data)

def save_result(telegram_id, data):
    rows = []
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except Exception:
            rows = []
    data["telegram_id"] = telegram_id
    data["timestamp"] = datetime.now().isoformat()
    data["completed"] = True
    rows.append(data)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def has_user_completed(uid):
    if not os.path.exists(RESULTS_FILE):
        return False
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return False
    return any(str(r.get("telegram_id")) == str(uid) and r.get("completed") for r in rows)


# =========================
# متن‌ها و واترمارک
# =========================
def welcome_text():
    return (
        "🎧 اگر ویس رو گوش دادی، حالا وقتشه…\n"
        "یک نفس عمیق بکش و وارد جهان درون خودت شو.\n"
        "این فقط یک تست نیست — این یه آینه‌ست.\n"
        "آینه‌ای که بی‌رحمانه، اما صادقانه، ضعف‌ها، ترس‌ها و الگوهای پنهان ذهنی‌ت رو نشون میده.\n"
        "الگوهایی که شاید تا امروز، بی‌آنکه بدونی، روی تصمیماتت در بازار تأثیر گذاشتن.\n"
        "اینجا خبری از شانس نیست…\n"
        "فقط تو، ذهن تو، و حقیقت درونی تو هست.\n"
        "اگه آماده‌ای با خودِ واقعی‌ت روبه‌رو بشی —\n\n"
        "تست رو شروع کن."
    )

def draw_watermark(c, PAGE_W, PAGE_H):
    c.saveState()
    c.setFont("Vazir", 40)
    c.setFillColorRGB(0.9, 0.9, 0.9)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(30)
    c.drawCentredString(0, 0, "Trade Therapist")
    c.restoreState()


# =========================
# عضویت کانال
# =========================
def is_user_member(user_id):
    try:
        st = bot.get_chat_member(CHANNEL_USERNAME, user_id).status
        return st in ("creator", "administrator", "member")
    except Exception as e:
        print("DEBUG is_user_member:", e)
        return False


# =========================
# سوال/کیبورد
# =========================
def build_question_keyboard(stage_data, q_id, can_go_prev):
    kb = types.InlineKeyboardMarkup()
    btns = []
    for ans in stage_data["answer_scale"]:
        val = ans["value"]
        lbl = ans["label"]
        cb = f"answer|{stage_data['stage_id']}|{q_id}|{val}"
        btns.append(types.InlineKeyboardButton(f"{val} - {lbl}", callback_data=cb))
    kb.row(*btns[:3])
    kb.row(*btns[3:])
    if can_go_prev:
        kb.add(types.InlineKeyboardButton("⬅️ سوال قبلی", callback_data=f"prev|{stage_data['stage_id']}"))
    return kb

def render_question(uid):
    st = get_user_state(uid)
    stage_id = st["stage"]
    data = load_stage(stage_id)
    questions = data["questions"]

    idx = st["current_q_index"]
    if idx >= len(questions):
        handle_stage_completed(uid, stage_id)
        return

    q_obj = questions[idx]
    q_id  = int(q_obj["id"])       # ✅ تضمین int بودن ID
    q_txt = q_obj["text"]

    total = len(questions)
    cur   = idx + 1
    prev  = (st["answers_stage1"].get(q_id) if stage_id == 1 else st["answers_stage2"].get(q_id))

    prog = int((cur / total) * 100)
    blocks = round(prog / 10)
    bar = "█" * blocks + "░" * (10 - blocks)

    msg = (
        f"سؤال {cur} از {total}\n"
        f"[{bar}] {prog}%\n\n"
        f"{q_txt}\n\n"
        "۱ = کاملاً مخالف ... ۵ = کاملاً موافق"
    )
    if prev is not None:
        msg += f"\n\nپاسخ فعلی شما: {prev}\nبرای تغییر، دوباره انتخاب کن."

    kb = build_question_keyboard(data, q_id, can_go_prev=(idx > 0))
    if st["last_message_id"] is None:
        sent = bot.send_message(uid, msg, reply_markup=kb)
        st["last_message_id"] = sent.message_id
    else:
        try:
            bot.edit_message_text(msg, chat_id=uid, message_id=st["last_message_id"])
            bot.edit_message_reply_markup(chat_id=uid, message_id=st["last_message_id"], reply_markup=kb)
        except Exception as e:
            print("DEBUG edit question:", e)
            sent = bot.send_message(uid, msg, reply_markup=kb)
            st["last_message_id"] = sent.message_id


# =========================
# جریان مراحل
# =========================
def start_stage(uid, stage_id):
    st = get_user_state(uid)

    if stage_id == 1 and st["finished_stage1"]:
        bot.send_message(uid, "مرحله پایه قبلاً تکمیل شده ✅")
        return
    if stage_id == 2 and st["finished_stage2"]:
        bot.send_message(uid, "مرحله پیشرفته قبلاً تکمیل شده ✅")
        return

    st["stage"] = stage_id
    st["current_q_index"] = 0
    st["last_message_id"] = None

    if stage_id == 1:
        bot.send_message(uid, "مرحله پایه شروع شد ✅\nهر جمله را از ۱ تا ۵ امتیاز بده.")
    else:
        bot.send_message(uid, "مرحله پیشرفته شروع شد 🔍\nپاسخ‌ها همچنان از ۱ تا ۵ هستن.")

    render_question(uid)

def handle_answer(uid, stage_id, q_id, value):
    st = get_user_state(uid)
    if stage_id == 1:
        st["answers_stage1"][q_id] = value
    else:
        st["answers_stage2"][q_id] = value
    st["current_q_index"] += 1
    render_question(uid)

def handle_prev(uid, stage_id):
    st = get_user_state(uid)
    if st["current_q_index"] > 0:
        st["current_q_index"] -= 1
    render_question(uid)


# =========================
# امتیازدهی
# =========================
def summarize_stage1(uid):
    st = get_user_state(uid)
    answers = st["answers_stage1"]
    data = load_stage(1)

    dims   = data["dimensions"]
    ranges = data["scoring"]["level_ranges"]
    rev    = set(data.get("scoring", {}).get("reverse_items", []))
    scale_min = data.get("scoring", {}).get("range", {}).get("min", 1)
    scale_max = data.get("scoring", {}).get("range", {}).get("max", 5)

    res = {}
    for dim_key, dim_info in dims.items():
        q_ids = dim_info["question_indexes"]
        total = 0
        for qn in q_ids:
            # ✅ سازگاری با کلیدهای int و str
            raw = answers.get(qn)
            if raw is None:
                raw = answers.get(str(qn))
            if raw is None:
                raw = 0
            if qn in rev and raw:
                raw = (scale_min + scale_max + 1) - raw  # 6 - raw برای 1..5
            total += raw

        level_label = ""
        level_mean  = ""
        for rng in ranges:
            if rng["range_min"] <= total <= rng["range_max"]:
                level_label = rng["level"]
                level_mean  = rng["meaning"]
                break
        # ✅ fallback اگر خارج بازه بود
        if not level_label:
            if total < ranges[0]["range_min"]:
                level_label, level_mean = ranges[0]["level"], ranges[0]["meaning"]
            elif total > ranges[-1]["range_max"]:
                level_label, level_mean = ranges[-1]["level"], ranges[-1]["meaning"]

        res[dim_key] = {
            "fa_title": dim_info.get("fa_title", dim_key),
            "score_total": total,
            "level_label": level_label,
            "level_meaning": level_mean,
            "focus": dim_info.get("focus", "")
        }

    outro = (
        "مرحله پایه تموم شد ✅\n\n"
        "حالا وارد مرحله دوم میشیم\n"
        "جایی که قراره به اعماق ذهنت سفر کنیم\n"
        "آیا حاضری ؟\n\n"
        "برای ورود به مرحله پیشرفته باید عضو کانال باشی.\n"
        "بعد از عضویت، روی دکمه ادامه بزن 🌿"
    )
    return outro, res

def summarize_stage2(uid):
    st = get_user_state(uid)
    answers = st["answers_stage2"]
    data = load_stage(2)

    dims  = data["dimensions"]
    scale = data["scoring"]["scale_definition"]

    # معکوس‌های stage2 اگر در questions تعریف شده باشد
    rev_flags = {}
    try:
        q_map = {int(q["id"]): q for q in data.get("questions", [])}
        for qid, q in q_map.items():
            if q.get("reverse"):
                rev_flags[qid] = True
    except Exception:
        pass

    scale_min = data.get("scoring", {}).get("range", {}).get("min", 1)
    scale_max = data.get("scoring", {}).get("range", {}).get("max", 5)

    res = {}
    for dim_key, dim_info in dims.items():
        q_ids = dim_info["question_indexes"]
        vals = []
        for qn in q_ids:
            raw = answers.get(qn)
            if raw is None:
                raw = answers.get(str(qn))
            if raw is None:
                continue
            if rev_flags.get(qn):
                raw = (scale_min + scale_max + 1) - raw
            vals.append(raw)
        avg = (sum(vals) / len(vals)) if vals else 0.0

        level_label = ""
        level_mean  = ""
        for lv in scale:
            if lv["range_min"] <= avg <= lv["range_max"]:
                level_label = lv["level"]
                level_mean  = lv["meaning"]
                break

        res[dim_key] = {
            "fa_title": dim_info.get("fa_title", dim_key),
            "avg_score": round(avg, 2),
            "level_label": level_label,
            "level_meaning": level_mean,
            "focus": dim_info.get("focus", "")
        }
    return res


# =========================
# تیپ و نگاشت سطح
# =========================
def normalize_level(lvl_raw):
    mapping = {
        "پایین": "Low", "کم": "Low", "LOW": "Low", "Low": "Low",
        "میانه": "Medium", "متوسط": "Medium", "MEDIUM": "Medium", "Medium": "Medium",
        "بالا": "High", "زیاد": "High", "HIGH": "High", "High": "High",
    }
    return mapping.get(lvl_raw, lvl_raw)

def build_profile_key(stage2_report):
    order = ["decision_making", "risk_reward", "cog_emotional", "growth_mindset"]
    levels = []
    for k in order:
        lvl = stage2_report.get(k, {}).get("level_label", "")
        levels.append(normalize_level(lvl))
    return "-".join(levels)

LEVEL_FA_MAP = {"High":"بالا","Medium":"متوسط","Low":"پایین","HIGH":"بالا","MEDIUM":"متوسط","LOW":"پایین"}


# =========================
# نمودارها
# =========================
def build_charts(uid, stage2_report):
    dm = stage2_report.get("decision_making", {}).get("avg_score", 0)
    rr = stage2_report.get("risk_reward", {}).get("avg_score", 0)
    ce = stage2_report.get("cog_emotional", {}).get("avg_score", 0)
    gm = stage2_report.get("growth_mindset", {}).get("avg_score", 0)

    raw_labels = ["تصمیم‌گیری تحت فشار", "ریسک / پاداش", "هماهنگی شناخت و هیجان", "ذهنیت رشد"]
    vals = [dm, rr, ce, gm]

    def rtl(txt):
        return get_display(arabic_reshaper.reshape(txt))
    labels_rtl = [rtl(x) for x in raw_labels]

    # Radar
    angles = [n / float(len(vals)) * 2 * pi for n in range(len(vals))]
    angles += angles[:1]
    vv = vals + vals[:1]

    fig = plt.figure(figsize=(4,4))
    ax = plt.subplot(111, polar=True)
    ax.set_theta_offset(pi/2); ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1]); ax.set_xticklabels(labels_rtl, fontdict={"fontsize":8})
    ax.set_rlabel_position(0)
    ax.set_yticks([1,2,3,4,5]); ax.set_yticklabels(["1","2","3","4","5"], fontsize=7)
    ax.set_ylim(0,5)
    ax.plot(angles, vv, linewidth=2)
    ax.fill(angles, vv, alpha=0.2)
    radar_path = os.path.join(CHARTS_DIR, f"radar_{uid}.png")
    plt.tight_layout(); plt.savefig(radar_path, dpi=150); plt.close(fig)

    # Bar
    fig2, ax2 = plt.subplots(figsize=(4,3))
    ax2.bar(range(len(vals)), vals)
    ax2.set_xticks(range(len(vals))); ax2.set_xticklabels(labels_rtl, rotation=20, ha="right", fontsize=8)
    ax2.set_ylim(0,5)
    ax2.set_ylabel(rtl("میانگین (۱ تا ۵)"), fontsize=8)
    ax2.set_title(rtl("میانگین هر بُعد در مرحله پیشرفته"), fontsize=9)
    bar_path = os.path.join(CHARTS_DIR, f"bar_{uid}.png")
    plt.tight_layout(); plt.savefig(bar_path, dpi=150); plt.close(fig2)

    return radar_path, bar_path


# =========================
# ابزار نوشتن فارسی روی PDF
# =========================
def _split_lines(text, max_chars):
    if not text: return []
    words = text.strip().split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars:
            if cur.strip(): lines.append(cur.strip())
            cur = w
        else:
            cur = w if not cur else f"{cur} {w}"
    if cur.strip(): lines.append(cur.strip())
    return lines

def draw_rtl_paragraph(c, text, x_right, y_top, max_width_chars, line_height, font_size):
    c.setFont("Vazir", font_size)
    y = y_top
    for ln in _split_lines(text or "", max_width_chars):
        vis = get_display(arabic_reshaper.reshape(ln))
        c.drawRightString(x_right, y, vis)
        y -= line_height
    return y

def draw_ltr_paragraph(c, text, x_left, y_top, max_chars_per_line, line_height, font_size):
    c.setFont("Vazir", font_size)
    y = y_top
    for ln in _split_lines(text or "", max_chars_per_line):
        c.drawString(x_left, y, ln)
        y -= line_height
    return y


# =========================
# PDF + خلاصه ادمین
# =========================
def generate_user_pdf(uid, user_obj):
    st = get_user_state(uid)
    stage2_report = st.get("stage2_report", {})

    with open(DIMENSIONS_FILE, "r", encoding="utf-8") as f:
        dim_lib = json.load(f)
    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        profiles_lib = json.load(f)
    with open(REPORT_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        tpl = json.load(f)

    fn = getattr(user_obj, "first_name", "") or ""
    ln = getattr(user_obj, "last_name", "") or ""
    full_name = f"{fn} {ln}".strip() or "نام ثبت نشده"
    un = getattr(user_obj, "username", None)
    username_val = f"@{un}" if un else "ندارد"
    report_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    profile_key = build_profile_key(stage2_report)
    profile_data = profiles_lib.get(profile_key, {})
    profile_title = profile_data.get("profile_title", "تیپ معاملاتی شما")

    radar_path, bar_path = build_charts(uid, stage2_report)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"report_{uid}_{timestamp}.pdf"
    pdf_path = os.path.join(REPORTS_DIR, pdf_filename)

    PAGE_W, PAGE_H = A4
    margin_left, margin_right = 40, A4[0] - 40
    MAX_CHARS = 55
    LH_BIG, LH_STD = 18, 16
    FS_TITLE, FS_SUB, FS_BODY = 16, 13, 11

    c = canvas.Canvas(pdf_path, pagesize=A4)

    # صفحه ۱
    draw_watermark(c, PAGE_W, PAGE_H)
    y = PAGE_H - 60
    header = tpl.get("header", {})

    y = draw_rtl_paragraph(c, header.get("title", "پروفایل شخصیت معاملاتی شما"), margin_right, y, MAX_CHARS, LH_BIG, FS_TITLE)
    y -= 8
    y = draw_rtl_paragraph(c, header.get("branding", "Trade Therapist • 2025"), margin_right, y, MAX_CHARS, LH_STD, FS_BODY)
    y = draw_rtl_paragraph(c, header.get("disclaimer", "این گزارش توصیه مستقیم مالی نیست."), margin_right, y, MAX_CHARS, LH_STD, 9)
    y -= 20

    identity = tpl.get("identity_block", {})
    y = draw_rtl_paragraph(c, identity.get("title", "مشخصات پروفایل شما"), margin_right, y, MAX_CHARS, LH_BIG, FS_SUB)
    y -= 10

    def write_kv(label_fa, value_txt):
        nonlocal y
        y = draw_rtl_paragraph(c, label_fa, margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 2
        if any(ch.isascii() and (ch.isalpha() or ch.isdigit() or ch in "@_-:.") for ch in value_txt):
            y = draw_ltr_paragraph(c, value_txt, margin_left, y, MAX_CHARS, LH_STD, FS_BODY)
        else:
            y = draw_rtl_paragraph(c, value_txt, margin_right, y, MAX_CHARS, LH_STD, FS_BODY)
        y -= 10

    write_kv("نام کاربر", full_name)
    write_kv("یوزرنیم", username_val)
    write_kv("تاریخ گزارش", report_date)
    write_kv("کد تیپ", profile_key)
    write_kv("تیپ نهایی", profile_title)
    y -= 10

    scores_block = tpl.get("scores_block", {})
    y = draw_rtl_paragraph(c, scores_block.get("title", "امتیازهای ذهنی و رفتاری شما"), margin_right, y, MAX_CHARS, LH_BIG, FS_SUB)
    y -= 10

    for dim_key, dim_info in get_user_state(uid).get("stage2_report", {}).items():
        dim_fa = dim_info.get("fa_title", dim_key)
        avg    = dim_info.get("avg_score", 0)
        lvlraw = dim_info.get("level_label", "")
        lvlf   = LEVEL_FA_MAP.get(lvlraw, lvlraw)
        y = draw_rtl_paragraph(c, dim_fa, margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 2
        y = draw_rtl_paragraph(c, f"میانگین {avg} از ۵", margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 2
        y = draw_rtl_paragraph(c, f"سطح: {lvlf}", margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 12

    # صفحه ۲
    c.showPage()
    draw_watermark(c, PAGE_W, PAGE_H)

    y = PAGE_H - 80
    y = draw_rtl_paragraph(c, "نمای کلی تصویری عملکرد ذهنی شما", margin_right, y, MAX_CHARS, LH_BIG, FS_SUB)

    y_base = PAGE_H - 200
    radar_y = y_base - 200
    bar_y   = y_base - 200

    try:
        c.drawImage(ImageReader(radar_path), margin_left, radar_y, width=220, preserveAspectRatio=True, mask='auto')
    except Exception as e:
        print("DEBUG radar insert:", e)
    try:
        c.drawImage(ImageReader(bar_path), margin_left + 240, bar_y, width=220, preserveAspectRatio=True, mask='auto')
    except Exception as e:
        print("DEBUG bar insert:", e)

    y_txt = radar_y - 40
    draw_rtl_paragraph(c, "این دو نمودار شدت و توازن هر بُعد (از ۱ تا ۵) را نشان می‌دهند.", margin_right, y_txt, MAX_CHARS, LH_STD, FS_BODY)

    # صفحه ۳+
    c.showPage()
    draw_watermark(c, PAGE_W, PAGE_H)

    y = PAGE_H - 60
    anal = tpl.get("dimensions_analysis_block", {})
    y = draw_rtl_paragraph(c, anal.get("title", "تحلیل چهاربعدی شما"), margin_right, y, MAX_CHARS, LH_BIG, FS_SUB); y -= 10

    with open(DIMENSIONS_FILE, "r", encoding="utf-8") as f:
        dim_lib2 = json.load(f)

    map_keys = {
        "decision_making": "DecisionMaking",
        "risk_reward": "RiskReward",
        "cog_emotional": "CognitiveEmotional",
        "growth_mindset": "GrowthMindset"
    }

    st2 = get_user_state(uid)["stage2_report"]
    for rep_key, dim_lib_key in map_keys.items():
        if y < 200:
            c.showPage(); draw_watermark(c, PAGE_W, PAGE_H); y = PAGE_H - 60
        user_dim = st2.get(rep_key, {})
        lvl_norm = normalize_level(user_dim.get("level_label", ""))
        dim_node = dim_lib2.get(dim_lib_key, {}).get(lvl_norm, {})
        title_fa = dim_node.get("label", dim_lib_key)
        analysis = dim_node.get("analysis", "")
        growth   = dim_node.get("growth_hint", "")
        y = draw_rtl_paragraph(c, title_fa, margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 4
        y = draw_rtl_paragraph(c, "تحلیل:", margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 2
        y = draw_rtl_paragraph(c, analysis, margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 6
        y = draw_rtl_paragraph(c, "مسیر رشد پیشنهادی:", margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 2
        y = draw_rtl_paragraph(c, growth, margin_right, y, MAX_CHARS, LH_STD, FS_BODY); y -= 12

    if y < 100:
        c.showPage(); draw_watermark(c, PAGE_W, PAGE_H); y = PAGE_H - 60

    footer = tpl.get("footer", {})
    y = draw_rtl_paragraph(c, footer.get("reminder", "هیچ تیپی «خوب/بد» نیست؛ آگاهی یعنی کنترل."), margin_right, y, MAX_CHARS, LH_STD, 9)
    y = draw_rtl_paragraph(c, footer.get("branding", "Trade Therapist • 2025"), margin_right, y, MAX_CHARS, LH_STD, 9)

    c.save()
    return pdf_path, profile_title, profile_key

def send_admin_overall_summary(uid, user_obj, stage1_report, stage2_report, profile_key, profile_title):
    def fa_level(label):
        return LEVEL_FA_MAP.get(label, label)

    uname = f"@{getattr(user_obj, 'username', None)}" if getattr(user_obj, "username", None) else "ندارد"
    full_name = f"{getattr(user_obj, 'first_name', '') or ''} {getattr(user_obj, 'last_name', '') or ''}".strip() or "نام ثبت نشده"
    when = datetime.now().strftime("%Y-%m-%d %H:%M")

    order_s1 = ["emotional","cognitive","behavioral","self_awareness","performance"]
    s1_lines = []
    for k in order_s1:
        info = (stage1_report or {}).get(k, {})
        if not info: continue
        fa = info.get("fa_title", k)
        tot = info.get("score_total", 0)
        lvl = fa_level(info.get("level_label",""))
        s1_lines.append(f"- {fa}: جمع {tot} | سطح {lvl}")

    order_s2 = ["decision_making","risk_reward","cog_emotional","growth_mindset"]
    s2_lines = []
    for k in order_s2:
        info = (stage2_report or {}).get(k, {})
        if not info: continue
        fa = info.get("fa_title", k)
        avg = info.get("avg_score", 0)
        lvl = fa_level(info.get("level_label",""))
        s2_lines.append(f"- {fa}: میانگین {avg} | سطح {lvl}")

    text = (
        "📬 گزارش کامل کاربر (ثبت نتیجه نهایی)\n"
        f"⏱ تاریخ: {when}\n"
        f"👤 نام: {full_name}\n"
        f"🔗 یوزرنیم: {uname}\n"
        f"🆔 آی‌دی: {uid}\n\n"
        "🧩 مرحله ۱ (جمع امتیاز هر بُعد):\n" + ("\n".join(s1_lines) if s1_lines else "—") + "\n\n"
        "🧪 مرحله ۲ (میانگین هر بُعد):\n" + ("\n".join(s2_lines) if s2_lines else "—") + "\n\n"
        f"🏷 تیپ نهایی: {profile_title}\n"
        f"🧾 کُد تیپ: {profile_key}"
    )
    notify_admins_text(text)


# =========================
# VOICE utils
# =========================
LEVEL_TO_FILENAME = {
    "low":"low","پایین":"low","کم":"low",
    "medium":"medium","میانه":"medium","متوسط":"medium",
    "high":"high","بالا":"high","زیاد":"high",
}

def _normalize_level_filename(level_label):
    if not level_label: return None
    key = str(level_label).strip().lower()
    return LEVEL_TO_FILENAME.get(key)

def voice_path_for_dim_level(dim_key, level_label):
    lvl = _normalize_level_filename(level_label)
    if not lvl:
        print(f"DEBUG voice_path: level not mapped -> {level_label!r} ({dim_key})")
        return ""
    cand = os.path.join(AUDIO_DIR, dim_key, f"{lvl}.ogg")
    if not os.path.exists(cand):
        print("DEBUG voice_path: missing file ->", cand)
        return ""
    return cand

def _send_voice_with_retry(chat_id, file_path, caption="", max_retries=3):
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            bot.send_chat_action(chat_id, "upload_voice")
            with open(file_path, "rb") as f:
                bot.send_voice(chat_id, f, caption=caption, timeout=120)
            return True
        except ApiTelegramException as e:
            retry_after = None
            try:
                retry_after = e.result_json.get("parameters", {}).get("retry_after")
            except Exception:
                pass
            if retry_after:
                print(f"DEBUG 429 retry after {retry_after}s for {os.path.basename(file_path)}")
                time.sleep(float(retry_after) + 0.5)
                continue
            last_err = e
            print("DEBUG send_voice ApiTelegramException:", e)
        except (ReadTimeout, ConnectTimeout, TimeoutError, socket.timeout) as e:
            last_err = e
            print(f"DEBUG send_voice timeout (attempt {attempt}):", e)
            time.sleep(2 * attempt)
        except Exception as e:
            last_err = e
            print("DEBUG send_voice error:", e)
            break
    try:
        bot.send_chat_action(chat_id, "upload_document")
        with open(file_path, "rb") as f:
            bot.send_document(chat_id, f, caption=caption, timeout=120)
        return True
    except Exception as e:
        print("DEBUG fallback send_document:", e)
        if last_err: print("DEBUG original error:", last_err)
        return False

def send_stage1_voice_feedback(uid):
    st = get_user_state(uid)
    report = st.get("stage1_report", {})
    if not report:
        print("DEBUG stage1_voice: empty report")
        return

    ordered_dims = ["emotional", "cognitive", "behavioral", "self_awareness", "performance"]
    for dim_key in ordered_dims:
        info = report.get(dim_key)
        if not info:
            print(f"DEBUG stage1_voice: missing dim -> {dim_key}")
            continue

        fa_title = info.get("fa_title", dim_key)
        lvl_label = info.get("level_label", "")
        path = voice_path_for_dim_level(dim_key, lvl_label)
        if not path:
            print(f"DEBUG stage1_voice: skip (no file) dim={dim_key} level={lvl_label!r}")
            continue

        caption = f"بعد: {fa_title}\nسطح فعلی: {lvl_label}"
        ok = _send_voice_with_retry(uid, path, caption=caption, max_retries=3)
        if not ok:
            print(f"DEBUG stage1_voice: failed send {path}")

        time.sleep(VOICE_DELAY_SECONDS)

def send_welcome_voice(uid):
    p = os.path.join(AUDIO_DIR, "welcome.ogg")
    if not os.path.exists(p):
        print("DEBUG send_welcome_voice: no file")
        return
    _send_voice_with_retry(uid, p, caption="🎙️ خوش‌اومدی! لطفاً ویس رو گوش بده و بعد «شروع ارزیابی» رو بزن.", max_retries=3)


# =========================
# پایان مراحل و نتیجه
# =========================
def handle_stage_completed(uid, stage_id):
    st = get_user_state(uid)

    # حذف آخرین پیام سؤال
    last_q_mid = st.get("last_message_id")
    if last_q_mid:
        try:
            bot.delete_message(uid, last_q_mid)
        except Exception as e:
            print("DEBUG delete last question:", e)
    st["last_message_id"] = None

    if stage_id == 1:
        st["finished_stage1"] = True
        text_after, rep1 = summarize_stage1(uid)
        st["stage1_report"] = rep1

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔍 ادامه و ورود به مرحله پیشرفته", callback_data="go_stage2"))
        bot.send_message(uid, text_after + f"\n\nلینک کانال: {CHANNEL_USERNAME}", reply_markup=kb)

        # ارسال ۵ ویس
        send_stage1_voice_feedback(uid)

    elif stage_id == 2:
        st["finished_stage2"] = True
        st["stage2_report"] = summarize_stage2(uid)
        st["report_ready"] = True

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📥 دریافت نتیجه", callback_data="get_result"))
        bot.send_message(uid, "مرحله پیشرفته تموم شد ✅\nبرای دریافت گزارش شخصی‌سازی‌شده روی دکمه زیر بزن.", reply_markup=kb)

def finalize_and_send_result(uid):
    st = get_user_state(uid)
    if not st.get("report_ready"):
        bot.send_message(uid, "نتیجه آماده نیست یا مرحله دوم کامل نشده.")
        return

    save_result(uid, {
        "stage1": st.get("stage1_report", {}),
        "stage2": st.get("stage2_report", {}),
        "profile_key": build_profile_key(st.get("stage2_report", {}))
    })

    try:
        try:
            chat_info = bot.get_chat(uid)
        except Exception:
            class Dummy: pass
            chat_info = Dummy(); chat_info.first_name=""; chat_info.last_name=""; chat_info.username=None

        pdf_path, profile_title, profile_key = generate_user_pdf(uid, chat_info)

        # خلاصه‌ی ۹ امتیاز برای همه ادمین‌ها
        send_admin_overall_summary(
            uid=uid,
            user_obj=chat_info,
            stage1_report=st.get("stage1_report", {}),
            stage2_report=st.get("stage2_report", {}),
            profile_key=profile_key,
            profile_title=profile_title
        )

    except Exception as e:
        print("DEBUG PDF error:", e)
        bot.send_message(uid, "⚠️ خطا در ساخت PDF. دوباره امتحان کن.")
        return

    summary_line = f"پروفایل عملکردی شما: «{profile_title}»  |  کد تیپ: {profile_key}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📞 درخواست منتورینگ", callback_data="mentoring"))

    bot.send_message(uid, summary_line, reply_markup=kb)
    with open(pdf_path, "rb") as f:
        bot.send_document(uid, f, caption="📄 گزارش شخصی‌سازی‌شده شما")
    st["report_sent"] = True


def notify_admin_for_mentoring(uid, user_obj):
    st = get_user_state(uid)
    uname = f"@{user_obj.username}" if getattr(user_obj, "username", None) else "ندارد"
    full_name = f"{user_obj.first_name or ''} {user_obj.last_name or ''}".strip()

    weakest_stage1 = min(st.get("stage1_report", {}).values(), key=lambda x: x.get("score_total", 999999), default=None)
    weakest_stage2 = min(st.get("stage2_report", {}).values(), key=lambda x: x.get("avg_score", 999999.0), default=None)

    extra = ""
    if weakest_stage1 or weakest_stage2:
        extra += "\n\nجمع‌بندی سریع:\n"
        if weakest_stage1: extra += f"- ضعف پایه: {weakest_stage1['fa_title']} / {weakest_stage1['level_label']}\n"
        if weakest_stage2: extra += f"- تحت فشار: {weakest_stage2['fa_title']} / {weakest_stage2['level_label']}\n"

    info = (
        "درخواست منتورینگ جدید 📞\n\n"
        f"نام: {full_name}\n"
        f"یوزرنیم: {uname}\n"
        f"آی‌دی: {uid}\n"
        "کاربر هر دو مرحله را کامل کرده و الان درخواست کوچ دارد."
        f"{extra}"
    )
    notify_admins_text(info)


# =========================
# هندلرها
# =========================
@bot.message_handler(commands=["start"])
def on_start(msg):
    uid = msg.chat.id
    get_user_state(uid)

    if has_user_completed(uid):
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("📞 درخواست منتورینگ")
        bot.send_message(uid, "✅ نتیجه شما قبلاً ثبت شده است.", reply_markup=kb)
        return

    send_welcome_voice(uid)

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📝 شروع ارزیابی پایه")
    bot.send_message(uid, welcome_text(), reply_markup=kb)

@bot.message_handler(func=lambda m: m.text in ["📝 شروع ارزیابی پایه", "🚀 شروع ارزیابی پایه"])
def on_begin_stage1(msg):
    uid = msg.chat.id
    if has_user_completed(uid):
        bot.send_message(uid, "✅ شما قبلاً ارزیابی را کامل کرده‌اید و نتیجه نهایی ثبت شده است.")
        return
    start_stage(uid, 1)

@bot.callback_query_handler(func=lambda c: c.data == "go_stage2")
def on_go_stage2(call):
    uid = call.from_user.id
    if has_user_completed(uid):
        bot.answer_callback_query(call.id, "نتیجه شما ثبت شده است.")
        bot.send_message(uid, "✅ شما قبلاً ارزیابی را کامل کرده‌اید.")
        return
    if not is_user_member(uid):
        bot.answer_callback_query(call.id, "❗️ اول باید عضو کانال باشی")
        bot.send_message(uid, f"برای ورود به مرحله پیشرفته ابتدا عضو کانال شو:\n{CHANNEL_USERNAME}")
        return
    bot.answer_callback_query(call.id, "مرحله دوم شروع شد 🔍")
    start_stage(uid, 2)

@bot.callback_query_handler(func=lambda c: c.data == "get_result")
def on_get_result(call):
    uid = call.from_user.id
    bot.answer_callback_query(call.id, "در حال آماده‌سازی نتیجه…")
    finalize_and_send_result(uid)

@bot.callback_query_handler(func=lambda c: c.data.startswith("answer|"))
def on_answer(call):
    parts = call.data.split("|")
    if len(parts) != 4: return
    _, stg, qid, val = parts
    uid = call.from_user.id
    handle_answer(uid, int(stg), int(qid), int(val))
    bot.answer_callback_query(call.id, "ثبت شد ✅")

@bot.callback_query_handler(func=lambda c: c.data.startswith("prev|"))
def on_prev(call):
    parts = call.data.split("|")
    if len(parts) != 2: return
    _, stg = parts
    uid = call.from_user.id
    handle_prev(uid, int(stg))
    bot.answer_callback_query(call.id, "⬅️ برگشتیم")

@bot.callback_query_handler(func=lambda c: c.data == "mentoring")
def on_mentoring(call):
    uid = call.from_user.id
    st = get_user_state(uid)
    if st["mentoring_requested"]:
        bot.answer_callback_query(call.id, "قبلاً ثبت شده ✅")
        bot.send_message(uid, "درخواستت قبلاً برای منتور ارسال شده ✅")
        return
    st["mentoring_requested"] = True
    notify_admin_for_mentoring(uid, call.from_user)
    bot.answer_callback_query(call.id, "ثبت شد ✅")
    bot.send_message(uid, "درخواستت برای منتور ارسال شد ✅")

@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(msg):
    uid = msg.chat.id
    if has_user_completed(uid):
        bot.send_message(uid, "✅ نتیجه شما ثبت شده است.\nبرای منتورینگ از دکمه «📞 درخواست منتورینگ» استفاده کن.")
        return
    bot.send_message(uid, "برای شروع از دکمه زیر استفاده کن:\n📝 شروع ارزیابی پایه")


# =========================
# اجرا
# =========================
bot.infinity_polling()
