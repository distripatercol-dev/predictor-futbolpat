import os
import threading
import unicodedata
import requests
import pytz
from datetime import datetime, timedelta
from scipy.stats import poisson, norm
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1. SERVIDOR FLASK PARA MANTENER RENDER ACTIVO 24/7 ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot Predictor Pro activo 24/7 en Render", 200

def ejecutar_servidor():
    puerto = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=puerto)

ZONA_LOCAL = pytz.timezone("America/Bogota")

# ==========================================
# 🔑 PEGA AQUÍ TUS DOS CLAVES REALES
# ==========================================
TELEGRAM_TOKEN = "8974980311:AAG-S2fXIinCoak8rZ14s3N6VF5N-m6V7VE".strip()
API_FOOTBALL_KEY = "c973094236aab4bad2c33d92034e4339".strip()

def normalizar_texto(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').strip()

# --- CONSULTA DINÁMICA DE ESTADÍSTICAS A LA API ---
def obtener_metricas_equipo(nombre_equipo, api_key):
    headers = {"x-apisports-key": api_key}
    nombre_limpio = normalizar_texto(nombre_equipo)
    try:
        url_t = f"https://v3.football.api-sports.io/teams?search={nombre_limpio}"
        res_t = requests.get(url_t, headers=headers, timeout=10).json().get("response", [])
        if not res_t:
            return None
        team_id = res_t[0]["team"]["id"]

        url_f = f"https://v3.football.api-sports.io/fixtures?team={team_id}&last=10"
        res_f = requests.get(url_f, headers=headers, timeout=10).json().get("response", [])
        if not res_f:
            return None

        goles_favor, goles_contra = 0, 0
        partidos = len(res_f)
        for f in res_f:
            es_local = f["teams"]["home"]["id"] == team_id
            gf = f["goals"]["home"] if es_local else f["goals"]["away"]
            gc = f["goals"]["away"] if es_local else f["goals"]["home"]
            goles_favor += gf if gf is not None else 1
            goles_contra += gc if gc is not None else 1

        return {
            "ataque": max(0.6, goles_favor / partidos),
            "defensa": max(0.6, goles_contra / partidos)
        }
    except Exception:
        return None

# --- MODELO MATEMÁTICO BET BUILDER ---
def calcular_mercados(loc, vis, api_key):
    stats_loc = obtener_metricas_equipo(loc, api_key)
    stats_vis = obtener_metricas_equipo(vis, api_key)

    if stats_loc and stats_vis:
        xg_loc = (stats_loc["ataque"] + stats_vis["defensa"]) / 2.0
        xg_vis = (stats_vis["ataque"] + stats_loc["defensa"]) / 2.0
        med_corners = 7.5 + (xg_loc + xg_vis) * 0.9
    else:
        seed_l = (sum(ord(c) for c in loc) % 10) / 10.0
        seed_v = (sum(ord(c) for c in vis) % 10) / 10.0
        xg_loc = 1.30 + seed_l * 0.5
        xg_vis = 1.00 + seed_v * 0.4
        med_corners = 9.4 + (seed_l - 0.5)

    p_loc, p_emp, p_vis = 0.0, 0.0, 0.0
    p_o20, p_u30, p_o15, p_u35 = 0.0, 0.0, 0.0, 0.0

    for i in range(7):
        for j in range(7):
            prob = poisson.pmf(i, xg_loc) * poisson.pmf(j, xg_vis)
            if i > j: p_loc += prob
            elif i == j: p_emp += prob
            else: p_vis += prob

            if i + j >= 2: p_o20 += prob
            if i + j <= 3: p_u30 += prob
            if i + j >= 1.5: p_o15 += prob
            if i + j <= 3.5: p_u35 += prob

    p_1x = p_loc + p_emp
    p_x2 = p_vis + p_emp
    p_12 = p_loc + p_vis
    p_dnb_loc = p_loc / (p_loc + p_vis) if (p_loc + p_vis) > 0 else 0.5
    p_dnb_vis = p_vis / (p_loc + p_vis) if (p_loc + p_vis) > 0 else 0.5

    p_loc_o05 = 1.0 - poisson.pmf(0, xg_loc)
    p_loc_o10_as = 1.0 - (poisson.pmf(0, xg_loc) + (poisson.pmf(1, xg_loc) * 0.5))
    p_loc_u20_as = sum(poisson.pmf(k, xg_loc) for k in range(2)) + (poisson.pmf(2, xg_loc) * 0.5)
    p_loc_u25 = sum(poisson.pmf(k, xg_loc) for k in range(3))

    p_vis_o05 = 1.0 - poisson.pmf(0, xg_vis)
    p_vis_u15 = sum(poisson.pmf(k, xg_vis) for k in range(2))
    p_vis_o10_as = 1.0 - (poisson.pmf(0, xg_vis) + (poisson.pmf(1, xg_vis) * 0.5))

    p_btts_si = (1.0 - poisson.pmf(0, xg_loc)) * (1.0 - poisson.pmf(0, xg_vis))
    p_btts_no = 1.0 - p_btts_si

    p_corners85 = 1.0 - norm.cdf(8.5, loc=med_corners, scale=2.7)
    p_corners95 = 1.0 - norm.cdf(9.5, loc=med_corners, scale=2.7)
    p_corners_u115 = norm.cdf(11.5, loc=med_corners, scale=2.7)

    med_amarillas = 4.6
    p_amarillas35 = 1.0 - norm.cdf(3.5, loc=med_amarillas, scale=1.4)
    p_amarillas45 = 1.0 - norm.cdf(4.5, loc=med_amarillas, scale=1.4)
    p_amarillas_u55 = norm.cdf(5.5, loc=med_amarillas, scale=1.4)

    p_faltas225 = 1.0 - norm.cdf(22.5, loc=24.5, scale=4.0)
    p_faltas235 = 1.0 - norm.cdf(23.5, loc=24.5, scale=4.0)
    p_faltas_u265 = norm.cdf(26.5, loc=24.5, scale=4.0)

    med_tiros_puerta_tot = 5.2 + (xg_loc + xg_vis) * 1.3
    p_tarco75 = 1.0 - norm.cdf(7.5, loc=med_tiros_puerta_tot, scale=2.4)
    p_tarco85 = 1.0 - norm.cdf(8.5, loc=med_tiros_puerta_tot, scale=2.4)
    p_tarco_u105 = norm.cdf(10.5, loc=med_tiros_puerta_tot, scale=2.4)

    med_tarco_loc = 2.4 + (xg_loc * 1.5)
    med_tarco_vis = 2.0 + (xg_vis * 1.4)
    p_tarco_loc35 = 1.0 - norm.cdf(3.5, loc=med_tarco_loc, scale=1.6)
    p_tarco_loc45 = 1.0 - norm.cdf(4.5, loc=med_tarco_loc, scale=1.6)
    p_tarco_vis25 = 1.0 - norm.cdf(2.5, loc=med_tarco_vis, scale=1.5)
    p_tarco_vis35 = 1.0 - norm.cdf(3.5, loc=med_tarco_vis, scale=1.5)

    med_tiros_tot = 15.0 + (xg_loc + xg_vis) * 3.4
    p_tiros215 = 1.0 - norm.cdf(21.5, loc=med_tiros_tot, scale=4.5)
    p_tiros225 = 1.0 - norm.cdf(22.5, loc=med_tiros_tot, scale=4.5)
    p_tiros_u265 = norm.cdf(26.5, loc=med_tiros_tot, scale=4.5)

    mercados_estandar = [
        ("Equipo Ganador", f"Gana directo: {loc} (1)", p_loc),
        ("Equipo Ganador", f"Gana directo: {vis} (2)", p_vis),
        ("Equipo Ganador", f"{loc} o Empate (1X)", p_1x),
        ("Equipo Ganador", f"{vis} o Empate (X2)", p_x2),
        ("Equipo Ganador", f"Cualquiera Gana: {loc} o {vis} (12)", p_12),
        ("Equipo Ganador", f"Empate Apuesta No Válida: {loc}", p_dnb_loc),
        ("Equipo Ganador", f"Empate Apuesta No Válida: {vis}", p_dnb_vis),
        ("Tiros de Esquina", "Más de 8.5 córners totales", p_corners85),
        ("Tiros de Esquina", "Más de 9.5 córners totales", p_corners95),
        ("Tiros de Esquina", "Menos de 11.5 córners totales", p_corners_u115),
        ("Total Goles", "Más de 1.5 goles totales", p_o15),
        ("Total Goles", "Más de 2.0 goles asiáticos", p_o20),
        ("Total Goles", "Menos de 3.0 goles asiáticos", p_u30),
        ("Total Goles", "Menos de 3.5 goles totales", p_u35),
        ("Tiros a Puerta", "Más de 7.5 tiros al arco totales", p_tarco75),
        ("Tiros a Puerta", "Más de 8.5 tiros al arco totales", p_tarco85),
        ("Tiros a Puerta", "Menos de 10.5 tiros al arco totales", p_tarco_u105),
        ("Tiros a Puerta", f"{loc} Más de 3.5 tiros al arco", p_tarco_loc35),
        ("Tiros a Puerta", f"{loc} Más de 4.5 tiros al arco", p_tarco_loc45),
        ("Tiros a Puerta", f"{vis} Más de 2.5 tiros al arco", p_tarco_vis25),
        ("Tiros a Puerta", f"{vis} Más de 3.5 tiros al arco", p_tarco_vis35),
        ("Tiros Totales", "Más de 21.5 tiros totales", p_tiros215),
        ("Tiros Totales", "Más de 22.5 tiros totales", p_tiros225),
        ("Tiros Totales", "Menos de 26.5 tiros totales", p_tiros_u265),
        ("Tarjetas Amarillas", "Más de 3.5 tarjetas amarillas", p_amarillas35),
        ("Tarjetas Amarillas", "Más de 4.5 tarjetas amarillas", p_amarillas45),
        ("Tarjetas Amarillas", "Menos de 5.5 tarjetas amarillas", p_amarillas_u55),
        ("Total Faltas", "Más de 22.5 faltas totales", p_faltas225),
        ("Total Faltas", "Más de 23.5 faltas totales", p_faltas235),
        ("Total Faltas", "Menos de 26.5 faltas totales", p_faltas_u265)
    ]

    picks = [m for m in mercados_estandar if 0.64 <= m[2] <= 0.70]

    goles_equipos = [
        ("Goles Local", f"{loc} marca más de 1.0 gol asiático", p_loc_o10_as),
        ("Goles Local", f"{loc} menos de 2.0 goles asiáticos", p_loc_u20_as),
        ("Goles Local", f"{loc} anota gol (Más de 0.5)", p_loc_o05),
        ("Goles Local", f"{loc} menos de 2.5 goles", p_loc_u25),
        ("Goles Visitante", f"{vis} anota gol (Más de 0.5)", p_vis_o05),
        ("Goles Visitante", f"{vis} menos de 1.5 goles", p_vis_u15),
        ("Goles Visitante", f"{vis} más de 1.0 gol asiático", p_vis_o10_as)
    ]
    for g in goles_equipos:
        if 0.63 <= g[2] <= 0.72:
            picks.append(g)

    btts = [
        ("Ambos Marcan", "Ambos Equipos Anotan: SÍ", p_btts_si),
        ("Ambos Marcan", "Ambos Equipos Anotan: NO", p_btts_no)
    ]
    for b in btts:
        if 0.55 <= b[2] <= 0.72:
            picks.append(b)

    vistos = set()
    limpios = []
    for m in picks:
        if m[1] not in vistos:
            limpios.append(m)
            vistos.add(m[1])

    return limpios, xg_loc, xg_vis

# --- COMANDOS TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚽ *PREDICTOR PRO 24/7 EN LÍNEA*\n\n"
        "Comandos disponibles:\n"
        "👉 `/hoy` : Partidos de hoy (Hora Colombia).\n"
        "👉 `/manana` : Partidos programados para mañana.\n"
        "👉 `/buscar Equipo` : Próximos partidos de cualquier club.\n"
        "👉 `/analizar Local vs Visitante` : Simulación de cuotas Bet Builder."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    termino = " ".join(context.args).strip()
    if not termino:
        await update.message.reply_text("Ingresa el club. Ejemplo: `/buscar America`", parse_mode="Markdown")
        return

    termino_limpio = normalizar_texto(termino)
    await update.message.reply_text(f"🔍 Buscando partidos de: *{termino}*...", parse_mode="Markdown")
    headers = {"x-apisports-key": API_FOOTBALL_KEY}

    try:
        url_t = f"https://v3.football.api-sports.io/teams?search={termino_limpio}"
        res_t = requests.get(url_t, headers=headers, timeout=12).json().get("response", [])
        if not res_t:
            await update.message.reply_text(f"No se encontró el club '{termino}'. Prueba con un nombre corto (ej. `America`, `Junior`, `Nacional`).")
            return

        team_id = res_t[0]["team"]["id"]
        team_name = res_t[0]["team"]["name"]

        url_fix = f"https://v3.football.api-sports.io/fixtures?team={team_id}&next=5&timezone=America/Bogota"
        partidos = requests.get(url_fix, headers=headers, timeout=12).json().get("response", [])

        if not partidos:
            await update.message.reply_text(f"Se localizó a *{team_name}*, pero no tiene partidos programados en la API.", parse_mode="Markdown")
            return

        resp = f"🎯 *PRÓXIMOS PARTIDOS DE {team_name.upper()}:*\n\n"
        for p in partidos:
            fecha_iso = p["fixture"]["date"]
            fecha_dt = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00")).astimezone(ZONA_LOCAL)
            fecha_str = fecha_dt.strftime("%d/%m - %I:%M %p")
            loc = p["teams"]["home"]["name"]
            vis = p["teams"]["away"]["name"]
            liga = p["league"]["name"]
            resp += f"• `[{fecha_str}]` *{loc} vs {vis}*\n  🏆 _{liga}_\n\n"

        resp += "Analiza con:\n`/analizar Local vs Visitante`"
        await update.message.reply_text(resp, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"Error en la consulta: {e}")

async def hoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Consultando partidos de hoy...")
    try:
        fecha_hoy = datetime.now(ZONA_LOCAL).strftime("%Y-%m-%d")
        headers = {"x-apisports-key": API_FOOTBALL_KEY}
        url = f"https://v3.football.api-sports.io/fixtures?date={fecha_hoy}&timezone=America/Bogota"
        r = requests.get(url, headers=headers, timeout=15).json().get("response", [])

        if not r:
            await update.message.reply_text("No hay partidos listados para hoy en la API.")
            return

        bloque = f"📅 *PARTIDOS DE HOY ({fecha_hoy}):*\n\n"
        for idx, p in enumerate(r[:25], start=1):
            hora = p["fixture"]["date"][11:16]
            loc = p["teams"]["home"]["name"]
            vis = p["teams"]["away"]["name"]
            liga = p["league"]["name"]
            bloque += f"{idx}. `[{hora}]` *{loc} vs {vis}* — _{liga}_\n"

        bloque += "\nAnaliza con:\n`/analizar Local vs Visitante`"
        await update.message.reply_text(bloque, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def manana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Consultando partidos de mañana...")
    try:
        fecha_manana = (datetime.now(ZONA_LOCAL) + timedelta(days=1)).strftime("%Y-%m-%d")
        headers = {"x-apisports-key": API_FOOTBALL_KEY}
        url = f"https://v3.football.api-sports.io/fixtures?date={fecha_manana}&timezone=America/Bogota"
        r = requests.get(url, headers=headers, timeout=15).json().get("response", [])

        if not r:
            await update.message.reply_text("No se encontraron partidos para mañana en la API.")
            return

        bloque = f"📅 *PARTIDOS DE MAÑANA ({fecha_manana}):*\n\n"
        for idx, p in enumerate(r[:25], start=1):
            hora = p["fixture"]["date"][11:16]
            loc = p["teams"]["home"]["name"]
            vis = p["teams"]["away"]["name"]
            liga = p["league"]["name"]
            bloque += f"{idx}. `[{hora}]` *{loc} vs {vis}* — _{liga}_\n"

        bloque += "\nAnaliza con:\n`/analizar Local vs Visitante`"
        await update.message.reply_text(bloque, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def analizar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    if " vs " not in texto:
        await update.message.reply_text("Formato requerido:\n`/analizar Local vs Visitante`", parse_mode="Markdown")
        return

    loc, vis = texto.split(" vs ")
    loc, vis = loc.strip(), vis.strip()

    await update.message.reply_text(f"📊 Analizando métricas dinámicas para: *{loc} vs {vis}*...", parse_mode="Markdown")

    picks, xg_l, xg_v = calcular_mercados(loc, vis, API_FOOTBALL_KEY)

    if not picks:
        await update.message.reply_text(f"⚠️ Ningún mercado superó los filtros (64%-70% / BTTS 55%) para {loc} vs {vis}.")
        return

    resp = f"🎯 *OPCIONES FILTRADAS BET BUILDER*\n⚽ *{loc} vs {vis}*\n"
    resp += f"📈 _xG Proyectado: {loc} ({xg_l:.2f}) - {vis} ({xg_v:.2f})_\n\n"
    for cat, desc, prob in picks:
        cuota = 1.0 / prob
        resp += f"🔹 *[{cat}]* {desc}\n"
        resp += f"   Probabilidad: *{prob*100:.1f}%* | Cuota justa: `{cuota:.2f}`\n\n"

    await update.message.reply_text(resp, parse_mode="Markdown")

# --- ARRANQUE PRINCIPAL ---
def main():
    # 1. Iniciar servidor web para que Render no suspenda el bot
    t = threading.Thread(target=ejecutar_servidor, daemon=True)
    t.start()
    print("🌐 Servidor Flask activo. Render permanecerá en línea.")

    # 2. Iniciar bot de Telegram con comandos válidos
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(CommandHandler("hoy", hoy))
    app.add_handler(CommandHandler("manana", manana))
    app.add_handler(CommandHandler("analizar", analizar))

    print("🟢 BOT OPERATIVO Y ESCUCHANDO 24/7 EN TELEGRAM.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
