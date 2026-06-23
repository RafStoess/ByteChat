from flask import Flask, request, redirect, session, url_for
from flask_socketio import SocketIO, send, join_room, leave_room
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes
)

import asyncio
import html
import os
import threading
import requests

from gemini_service import preguntar_ia


# =========================================
# CONFIG LOCAL
# =========================================

def cargar_config_local():

    ruta_config = os.path.join(
        os.path.dirname(__file__),
        ".env.local"
    )

    if not os.path.exists(ruta_config):

        return

    with open(ruta_config, "r", encoding="utf-8") as config:

        for linea in config:

            linea = linea.strip()

            if not linea or linea.startswith("#") or "=" not in linea:

                continue

            clave, valor = linea.split("=", 1)

            os.environ.setdefault(
                clave.strip().lstrip("\ufeff"),
                valor.strip().strip('"').strip("'")
            )


cargar_config_local()


# =========================================
# CONFIG TELEGRAM
# =========================================

tokenTelegram = os.environ.get(
    "TOKEN_TELEGRAM",
    ""
)

chatID = os.environ.get(
    "CHAT_ID",
    ""
)

adminUser = os.environ.get(
    "ADMIN_USER",
    ""
)

adminPassword = os.environ.get(
    "ADMIN_PASSWORD",
    ""
)

secretKey = os.environ.get(
    "SECRET_KEY",
    "bytechat-dev-secret-key"
)


# =========================================
# APP
# =========================================

app = Flask(__name__)

# En produccion configure SECRET_KEY como variable de entorno.
# El fallback existe solo para pruebas locales.
app.secret_key = secretKey

socket = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading"
)


# =========================================
# BOT TELEGRAM
# =========================================

# Se inicializa al final del modulo, despues de definir su manejador.
# Esto permite importar ByteChat:app desde Gunicorn sin perder Telegram.
botTelegram = None


# =========================================
# USUARIOS
# =========================================

usuarios = []
usuariosWeb = {}
usuariosTelegram = set()
salasBase = [
    "general",
    "anuncios",
    "soporte",
    "ia"
]
salasDinamicas = []
salasUsuario = {}
mensajesFijados = {
    "general": "",
    "anuncios": "",
    "soporte": "",
    "ia": ""
}
historialSalas = {
    "general": [],
    "anuncios": [],
    "soporte": [],
    "ia": []
}
tickets = {}
ticketContador = 0


def obtenerSalas():

    return salasBase + [
        sala
        for sala in salasDinamicas
        if sala not in salasBase
    ]


def normalizarSala(nombre):

    sala = str(nombre).strip().lower()

    reemplazos = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n"
    }

    for origen, destino in reemplazos.items():

        sala = sala.replace(origen, destino)

    limpia = []
    ultimo_guion = False

    for caracter in sala:

        if caracter.isalnum():

            limpia.append(caracter)
            ultimo_guion = False

        elif not ultimo_guion:

            limpia.append("-")
            ultimo_guion = True

    sala = "".join(limpia).strip("-")

    if not sala:

        return ""

    if not sala.startswith("equipo-"):

        sala = f"equipo-{sala}"

    return sala[:40]


def emitirSalas(destino=None):

    if destino:

        socket.emit(
            "salas",
            obtenerSalas(),
            to=destino
        )

        return

    socket.emit(
        "salas",
        obtenerSalas()
    )


def guardarHistorialSala(sala, mensaje):

    if sala not in historialSalas:

        historialSalas[sala] = []

    historialSalas[sala].append(mensaje)
    historialSalas[sala] = historialSalas[sala][-50:]


def obtenerHistorialSala(sala):

    return historialSalas.get(
        sala,
        []
    )


def emitirEstadoSala(sala, destino):

    if sala == "soporte":

        usuario = usuariosWeb.get(
            destino,
            ""
        )

        mensajes = []

        if usuario:

            ticket = asegurarTicketSoporteUsuario(
                usuario
            )

            join_room(
                f"ticket-{ticket['id']}",
                sid=destino,
                namespace="/"
            )

            mensajes = serializarMensajesSoporte(
                ticket
            )

            emitirTicketsUsuario(usuario)

            socket.emit(
                "soporte_estado",
                {
                    "bloqueado": ticket["estado"] == "bloqueado"
                },
                to=destino
            )

        socket.emit(
            "historial_sala",
            {
                "sala": sala,
                "mensajes": mensajes
            },
            to=destino
        )

        return

    socket.emit(
        "mensaje_fijado",
        {
            "sala": sala,
            "texto": mensajesFijados.get(
                sala,
                ""
            )
        },
        to=destino
    )

    socket.emit(
        "historial_sala",
        {
            "sala": sala,
            "mensajes": obtenerHistorialSala(sala)
        },
        to=destino
    )


def reiniciarChatEvento():

    salasDinamicas.clear()

    historialSalas.clear()

    for sala in salasBase:

        historialSalas[sala] = []

    mensajesFijados.clear()

    for sala in salasBase:

        mensajesFijados[sala] = ""

    for sid, sala in list(salasUsuario.items()):

        if sala not in salasBase:

            leave_room(
                sala,
                sid=sid,
                namespace="/"
            )

            join_room(
                "general",
                sid=sid,
                namespace="/"
            )

            salasUsuario[sid] = "general"

            socket.emit(
                "sala_actual",
                "general",
                to=sid
            )

            emitirEstadoSala(
                "general",
                sid
            )

    emitirSalas()

    socket.emit(
        "chat_reiniciado",
        {
            "mensaje": "El chat fue reiniciado por el administrador.",
            "salas": obtenerSalas()
        }
    )


def crearTicketId():

    global ticketContador

    ticketContador += 1

    return f"TICKET-{ticketContador:03d}"


def serializarTicket(ticket):

    return {
        "id": ticket["id"],
        "usuario": ticket["usuario"],
        "asunto": ticket["asunto"],
        "estado": ticket["estado"],
        "mensajes": ticket["mensajes"],
        "ultimo": (
            ticket["mensajes"][-1]["texto"]
            if ticket["mensajes"]
            else ""
        )
    }


def obtenerTicketsUsuario(usuario):

    return [
        serializarTicket(ticket)
        for ticket in tickets.values()
        if ticket["usuario"] == usuario
    ]


def obtenerTicketAbiertoUsuario(usuario):

    usuario_normalizado = usuario.strip().lower()

    for ticket in tickets.values():

        if (
            ticket["usuario"].strip().lower() == usuario_normalizado
            and ticket["estado"] in ("abierto", "bloqueado")
        ):

            return ticket

    return None


def asegurarTicketSoporteUsuario(usuario):

    ticket_abierto = obtenerTicketAbiertoUsuario(
        usuario
    )

    if ticket_abierto:

        return ticket_abierto

    ticket_id = crearTicketId()

    tickets[ticket_id] = {
        "id": ticket_id,
        "usuario": usuario,
        "asunto": "Soporte",
        "estado": "abierto",
        "mensajes": []
    }

    socket.emit(
        "tickets_admin_actualizados",
        True,
        to="admins"
    )

    return tickets[ticket_id]


def serializarMensajesSoporte(ticket):

    mensajes = []

    for mensaje in ticket.get("mensajes", []):

        autor = mensaje.get("autor", "")

        if autor == "Admin":

            autor = "Soporte ByteChat"

        mensajes.append(
            f"{autor}: {mensaje.get('texto', '')}"
        )

    return mensajes


def emitirTicketsUsuario(usuario):

    for sid, nombre in usuariosWeb.items():

        if nombre == usuario:

            socket.emit(
                "tickets_usuario",
                obtenerTicketsUsuario(usuario),
                to=sid
            )


def emitirTicketPrivado(ticket_id):

    ticket = tickets.get(ticket_id)

    if not ticket:

        return

    data = serializarTicket(ticket)
    room = f"ticket-{ticket_id}"

    socket.emit(
        "ticket_detalle",
        data,
        to=room
    )

    for sid, nombre in usuariosWeb.items():

        if nombre == ticket["usuario"]:

            socket.emit(
                "ticket_actualizado",
                data,
                to=sid
            )

            if salasUsuario.get(sid) == "soporte":

                socket.emit(
                    "historial_sala",
                    {
                        "sala": "soporte",
                        "mensajes": serializarMensajesSoporte(ticket)
                    },
                    to=sid
                )

                socket.emit(
                    "soporte_estado",
                    {
                        "bloqueado": ticket["estado"] == "bloqueado"
                    },
                    to=sid
                )

    socket.emit(
        "tickets_admin_actualizados",
        True,
        to="admins"
    )


def emitirUsuarios():

    usuarios.clear()
    vistos = set()

    for nombre in usuariosWeb.values():

        clave = nombre.lower()

        if clave in vistos:

            continue

        vistos.add(clave)

        usuarios.append({
            "nombre": nombre,
            "telegram": False
        })

    for nombre in sorted(usuariosTelegram):

        clave = nombre.lower()

        if clave in vistos:

            continue

        vistos.add(clave)

        usuarios.append({
            "nombre": nombre,
            "telegram": True
        })

    socket.emit(
        "usuarios",
        usuarios
    )


def registrarUsuarioTelegram(nombre):

    if not nombre:

        return

    usuariosTelegram.add(nombre)
    emitirUsuarios()


# =========================================
# COMANDOS AUTOMATICOS DE BYTEBOT
# =========================================

STATIC_DOCS_DIR = os.path.join(
    app.root_path,
    "static",
    "docs"
)

TEAMBOOK_FILENAME = "teambook.pdf"
TEAMBOOK_WEB_PATH = f"/static/docs/{TEAMBOOK_FILENAME}"
TEAMBOOK_FILE_PATH = os.path.join(
    STATIC_DOCS_DIR,
    TEAMBOOK_FILENAME
)


COMMAND_RESPONSES = {
    "/ayuda": (
        "\U0001f4cb BYTECHAT - COMANDOS DEL EVENTO\n\n"
        "Evento\n"
        "- /inscripcion\n"
        "- /requisitos\n"
        "- /horarios\n"
        "- /ubicacion\n"
        "- /reglas\n"
        "- /contacto\n\n"
        "Recursos\n"
        "- /teambook\n\n"
        "Algoritmos\n"
        "- /bfs\n"
        "- /dfs\n"
        "- /dijkstra\n"
        "- /dp\n"
        "- /greedy\n"
        "- /backtracking\n\n"
        "Escribe un comando para ver informacion resumida."
    ),
    "/info": (
        "\U0001f3c6 EVENTO DE PROGRAMACION COMPETITIVA 2026\n\n"
        "Bienvenido al asistente oficial del evento.\n\n"
        "Fecha: 15 de Agosto de 2026\n"
        "Hora: 09:00 AM\n"
        "Modalidad: Presencial\n"
        "Participacion: Individual o por equipos\n\n"
        "Este evento reunira estudiantes y entusiastas de la programacion "
        "competitiva para resolver problemas algoritmicos en un entorno "
        "desafiante y colaborativo.\n\n"
        "Para mas informacion:\n"
        "/inscripcion\n"
        "/cronograma\n"
        "/reglas"
    ),
    "/inscripcion": (
        "\U0001f4dd INSCRIPCION\n\n"
        "Para participar:\n\n"
        "1. Completa el formulario de inscripcion.\n"
        "2. Registra los integrantes del equipo.\n"
        "3. Espera la confirmacion de tu registro.\n\n"
        "Equipos:\n"
        "- 1 a 3 integrantes\n\n"
        "Fecha limite:\n"
        "10 de Agosto de 2026\n\n"
        "Consultas:\n"
        "Usa /contacto"
    ),
    "/requisitos": (
        "\U0001f4cb REQUISITOS DE PARTICIPACION\n\n"
        "- Ser estudiante o invitado registrado.\n"
        "- Contar con documento de identificacion.\n"
        "- Llevar computadora portatil.\n"
        "- Tener conocimientos basicos de programacion.\n\n"
        "Lenguajes recomendados:\n"
        "- C++\n"
        "- Java\n"
        "- Python\n\n"
        "Para mas detalles:\n"
        "/reglas"
    ),
    "/ubicacion": (
        "\U0001f4cd UBICACION DEL EVENTO\n\n"
        "Auditorio Principal\n"
        "Facultad de Ingenieria\n\n"
        "Google Maps:\n"
        "https://maps.app.goo.gl/4yG9mkKt3pUhmyjc9\n\n"
        "Recomendacion:\n"
        "Llegar al menos 30 minutos antes del inicio de la competencia."
    ),
    "/reglas": (
        "\u2696\ufe0f REGLAMENTO GENERAL\n\n"
        "Se permite:\n"
        "- Uso del TeamBook oficial\n"
        "- Documentacion impresa\n"
        "- Material proporcionado por la organizacion\n\n"
        "No se permite:\n"
        "- Uso de Inteligencia Artificial durante la competencia\n"
        "- Comunicacion con personas externas\n"
        "- Compartir soluciones entre equipos\n\n"
        "El incumplimiento puede ocasionar descalificacion."
    ),
    "/cronograma": (
        "\U0001f552 CRONOGRAMA\n\n"
        "08:30 - Registro de participantes\n"
        "09:00 - Inauguracion\n"
        "09:30 - Inicio de la competencia\n"
        "13:00 - Receso\n"
        "14:00 - Continuacion\n"
        "17:00 - Fin de la competencia\n"
        "17:30 - Premiacion\n\n"
        "Los horarios pueden estar sujetos a cambios."
    ),
    "/horarios": (
        "\U0001f552 HORARIOS\n\n"
        "08:30 - Registro de participantes\n"
        "09:00 - Inauguracion\n"
        "09:30 - Inicio de la competencia\n"
        "13:00 - Receso\n"
        "14:00 - Continuacion\n"
        "17:00 - Fin de la competencia\n"
        "17:30 - Premiacion\n\n"
        "Los horarios pueden estar sujetos a cambios."
    ),
    "/contacto": (
        "\U0001f4de CONTACTO OFICIAL\n\n"
        "Email:\n"
        "evento@universidad.edu\n\n"
        "WhatsApp:\n"
        "+591 70000000\n\n"
        "Telegram:\n"
        "@EventoProgramacion\n\n"
        "Horario de atencion:\n"
        "08:00 - 18:00"
    ),
    "/algoritmos": (
        "\U0001f9e0 ALGORITMOS DISPONIBLES\n\n"
        "/bfs\n"
        "/dfs\n"
        "/dijkstra\n"
        "/dp\n"
        "/greedy\n"
        "/backtracking\n"
        "/unionfind\n"
        "/segmenttree\n\n"
        "Estos comandos muestran una explicacion resumida de cada tecnica "
        "utilizada en programacion competitiva."
    ),
    "/bfs": (
        "\U0001f50e BFS - Breadth First Search\n\n"
        "Que es:\n"
        "Recorrido de grafos por niveles usando una cola.\n\n"
        "Cuando se usa:\n"
        "- Caminos minimos en grafos no ponderados\n"
        "- Busqueda por niveles\n"
        "- Problemas de estados\n\n"
        "Complejidad general:\n"
        "O(V + E), donde V son vertices y E aristas.\n\n"
        "Estructura conceptual:\n"
        "Marcar inicio, procesar una cola y agregar vecinos no visitados."
    ),
    "/dfs": (
        "\U0001f332 DFS - Depth First Search\n\n"
        "Que es:\n"
        "Recorrido de grafos que profundiza por una rama antes de retroceder.\n\n"
        "Cuando se usa:\n"
        "- Componentes conectados\n"
        "- Deteccion de ciclos\n"
        "- Backtracking\n"
        "- Ordenamiento topologico\n\n"
        "Complejidad general:\n"
        "O(V + E), donde V son vertices y E aristas.\n\n"
        "Estructura conceptual:\n"
        "Marcar el nodo actual y visitar recursivamente vecinos no visitados."
    ),
    "/dijkstra": (
        "\U0001f6e3\ufe0f DIJKSTRA\n\n"
        "Que es:\n"
        "Algoritmo para caminos minimos con pesos no negativos.\n\n"
        "Cuando se usa:\n"
        "- Rutas minimas\n"
        "- Grafos ponderados\n"
        "- Optimizacion de costos\n\n"
        "Complejidad general:\n"
        "O((V + E) log V) usando cola de prioridad.\n\n"
        "Estructura conceptual:\n"
        "Mantener distancias minimas, extraer el nodo mas cercano y relajar aristas."
    ),
    "/greedy": (
        "\u26a1 GREEDY\n\n"
        "Que es:\n"
        "Tecnica que toma una decision local conveniente en cada paso.\n\n"
        "Cuando se usa:\n"
        "- Intervalos\n"
        "- Seleccion de actividades\n"
        "- Huffman\n"
        "- Problemas de ordenamiento\n\n"
        "Complejidad general:\n"
        "Depende del problema; suele estar dominada por ordenar: O(n log n).\n\n"
        "Estructura conceptual:\n"
        "Ordenar o priorizar, elegir una opcion valida y avanzar sin recalcular todo."
    ),
    "/dp": (
        "\U0001f9e9 PROGRAMACION DINAMICA\n\n"
        "Que es:\n"
        "Tecnica que divide un problema en subproblemas y reutiliza resultados.\n\n"
        "Cuando se usa:\n"
        "- Mochila\n"
        "- Caminos\n"
        "- Subsecuencias\n"
        "- Optimizacion\n\n"
        "Complejidad general:\n"
        "Depende del numero de estados por transiciones.\n\n"
        "Estructura conceptual:\n"
        "Definir estado, transicion, casos base y orden de calculo."
    ),
    "/unionfind": (
        "\U0001f517 UNION FIND\n\n"
        "Union Find permite manejar conjuntos disjuntos eficientemente.\n\n"
        "Uso comun:\n"
        "- Componentes conectados\n"
        "- Kruskal\n"
        "- Deteccion de ciclos\n"
        "- Agrupacion de elementos\n\n"
        "Operaciones:\n"
        "- find(x): encuentra el representante.\n"
        "- union(a, b): une dos conjuntos."
    ),
    "/segmenttree": (
        "\U0001f333 SEGMENT TREE\n\n"
        "Segment Tree permite responder consultas sobre rangos de manera "
        "eficiente.\n\n"
        "Uso comun:\n"
        "- Suma en rangos\n"
        "- Minimo o maximo en rangos\n"
        "- Actualizaciones puntuales\n"
        "- Problemas con muchas consultas\n\n"
        "Estructura general:\n"
        "1. Construir el arbol.\n"
        "2. Consultar rangos.\n"
        "3. Actualizar valores."
    ),
    "/backtracking": (
        "\U0001f9ed BACKTRACKING\n\n"
        "Que es:\n"
        "Tecnica de busqueda que prueba opciones y retrocede si una rama no sirve.\n\n"
        "Cuando se usa:\n"
        "- Permutaciones\n"
        "- Combinaciones\n"
        "- Sudoku\n"
        "- Busqueda exhaustiva con poda\n\n"
        "Complejidad general:\n"
        "Usualmente exponencial; mejora con podas y restricciones.\n\n"
        "Estructura conceptual:\n"
        "Elegir opcion, avanzar, validar, retroceder y podar ramas invalidas."
    )
}


UNKNOWN_COMMAND_RESPONSE = (
    "\u2753 COMANDO NO RECONOCIDO\n\n"
    "Ese comando no existe en ByteChat.\n"
    "Usa /ayuda para ver la lista de comandos disponibles."
)


def get_teambook_response():

    if not os.path.exists(TEAMBOOK_FILE_PATH):

        return {
            "command": "/teambook",
            "text": (
                "El TeamBook aun no esta disponible. "
                "Intenta nuevamente mas tarde."
            ),
            "document_path": None,
            "document_url": TEAMBOOK_WEB_PATH
        }

    return {
        "command": "/teambook",
        "text": (
            "\U0001f4da TEAMBOOK OFICIAL\n\n"
            "El TeamBook contiene material de apoyo para la competencia:\n\n"
            "- Grafos\n"
            "- Programacion Dinamica\n"
            "- Estructuras de Datos\n"
            "- Matematica Computacional\n"
            "- Tecnicas de Programacion Competitiva\n\n"
            "Descargar TeamBook:\n"
            f"{TEAMBOOK_WEB_PATH}"
        ),
        "document_path": TEAMBOOK_FILE_PATH,
        "document_url": TEAMBOOK_WEB_PATH
    }


def get_command_response(message):

    command = message.strip().lower()

    if command == "/teambook":

        return get_teambook_response()

    response = COMMAND_RESPONSES.get(command)

    if not response:

        if command.startswith("/"):

            return {
                "command": command,
                "text": UNKNOWN_COMMAND_RESPONSE,
                "document_path": None,
                "document_url": None
            }

        return None

    return {
        "command": command,
        "text": response,
        "document_path": None,
        "document_url": None
    }


# =========================================
# HTML
# =========================================

def renderAdminLogin(error=""):

    error_html = ""

    if error:

        error_html = f"<p class='error'>{html.escape(error)}</p>"

    return f"""
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ByteChat Admin</title>
<style>
body{{
    margin:0;
    min-height:100vh;
    display:grid;
    place-items:center;
    background:#0f172a;
    color:#f8fafc;
    font-family:Segoe UI, sans-serif;
}}
.admin-card{{
    width:min(92vw, 380px);
    padding:24px;
    border:1px solid #243041;
    border-radius:12px;
    background:#111827;
}}
h1{{margin-bottom:18px;}}
label{{display:block;margin:12px 0 6px;color:#cbd5e1;}}
input{{
    width:100%;
    padding:12px;
    border:none;
    border-radius:8px;
    background:#1e293b;
    color:white;
    box-sizing:border-box;
}}
button{{
    width:100%;
    margin-top:18px;
    padding:12px;
    border:none;
    border-radius:8px;
    background:#06b6d4;
    color:white;
    font-weight:700;
    cursor:pointer;
}}
.error{{color:#fca5a5;margin-bottom:12px;}}
</style>
</head>
<body>
<form class="admin-card" method="post" action="/admin">
    <h1>ByteChat Admin</h1>
    {error_html}
    <label for="usuario">Usuario</label>
    <input id="usuario" name="usuario" autocomplete="username" required>
    <label for="password">Contraseña</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Ingresar</button>
</form>
</body>
</html>
"""


def renderAdminDashboard(
    mensaje="",
    sala_seleccionada="anuncios",
    ticket_seleccionado=""
):

    mensaje_html = ""

    if mensaje:

        mensaje_html = f"<p class='notice'>{html.escape(mensaje)}</p>"

    fijado = html.escape(
        mensajesFijados.get(
            sala_seleccionada,
            ""
        )
    )

    general_selected = (
        "selected"
        if sala_seleccionada == "general"
        else ""
    )

    anuncios_selected = (
        "selected"
        if sala_seleccionada == "anuncios"
        else ""
    )

    tickets_html = ""

    for ticket in tickets.values():

        if ticket["estado"] not in ("abierto", "bloqueado"):

            continue

        ultimo = html.escape(
            ticket["mensajes"][-1]["texto"]
            if ticket["mensajes"]
            else ""
        )

        cantidad_mensajes = len(
            ticket["mensajes"]
        )

        tickets_html += f"""
        <div class="ticket-row">
            <strong>{html.escape(ticket["usuario"])}</strong>
            <span>{cantidad_mensajes} mensajes</span>
            <small>{ultimo}</small>
            <form method="get" action="/admin/dashboard">
                <button name="ticket" value="{html.escape(ticket["id"])}" type="submit">
                    Abrir conversacion
                </button>
            </form>
        </div>
        """

    if not tickets_html:

        tickets_html = "<p class='current'>No hay tickets abiertos de soporte.</p>"

    ticket_detalle_html = ""
    ticket_actual = tickets.get(ticket_seleccionado)

    if ticket_actual:

        mensajes_ticket = ""

        for mensaje_ticket in ticket_actual["mensajes"]:

            mensajes_ticket += (
                "<p class='ticket-message'>"
                f"<strong>{html.escape(mensaje_ticket['autor'])}:</strong> "
                f"{html.escape(mensaje_ticket['texto'])}"
                "</p>"
            )

        respuesta_form = ""

        if ticket_actual["estado"] in ("abierto", "bloqueado"):

            accion_bloqueo = (
                "desbloquear_chat"
                if ticket_actual["estado"] == "bloqueado"
                else "bloquear_chat"
            )

            texto_bloqueo = (
                "🔓 Desbloquear chat"
                if ticket_actual["estado"] == "bloqueado"
                else "🔒 Bloquear chat"
            )

            respuesta_form = f"""
            <form method="post" action="/admin/dashboard">
                <input type="hidden" name="ticket_id" value="{html.escape(ticket_actual["id"])}">
                <textarea name="respuesta_admin" placeholder="Responder al ticket..."></textarea>
                <button name="accion" value="responder_ticket_admin" type="submit">
                    Responder
                </button>
                <button class="danger" name="accion" value="{accion_bloqueo}" type="submit">
                    {texto_bloqueo}
                </button>
            </form>
            """
        else:

            respuesta_form = "<p class='current'>Esta conversacion no esta activa.</p>"

        ticket_detalle_html = f"""
        <div class="ticket-detail">
            <h3>{html.escape(ticket_actual["id"])} - {html.escape(ticket_actual["asunto"])}</h3>
            <p>Usuario: {html.escape(ticket_actual["usuario"])} | Estado: {html.escape(ticket_actual["estado"])}</p>
            <div>{mensajes_ticket}</div>
            {respuesta_form}
        </div>
        """

    return f"""
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Panel Admin - ByteChat</title>
<style>
body{{
    margin:0;
    min-height:100vh;
    background:#0f172a;
    color:#f8fafc;
    font-family:Segoe UI, sans-serif;
}}
.admin-shell{{
    max-width:900px;
    margin:0 auto;
    padding:24px;
}}
.topbar{{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:12px;
    margin-bottom:20px;
}}
a{{color:#67e8f9;}}
.panel{{
    border:1px solid #243041;
    border-radius:12px;
    background:#111827;
    padding:18px;
    margin-bottom:16px;
}}
textarea{{
    width:100%;
    min-height:110px;
    padding:12px;
    border:none;
    border-radius:8px;
    background:#1e293b;
    color:white;
    box-sizing:border-box;
    resize:vertical;
}}
select{{
    width:100%;
    padding:12px;
    border:none;
    border-radius:8px;
    background:#1e293b;
    color:white;
    box-sizing:border-box;
    margin-bottom:12px;
}}
button{{
    margin-top:12px;
    padding:11px 14px;
    border:none;
    border-radius:8px;
    background:#06b6d4;
    color:white;
    font-weight:700;
    cursor:pointer;
}}
.danger{{background:#ef4444;}}
.notice{{
    color:#86efac;
    margin-bottom:14px;
}}
.current{{
    white-space:pre-wrap;
    color:#cbd5e1;
    background:#1e293b;
    padding:12px;
    border-radius:8px;
}}
.ticket-row{{
    display:grid;
    grid-template-columns:110px 1fr 1fr 90px 1.5fr auto;
    gap:8px;
    align-items:center;
    padding:10px;
    border-bottom:1px solid #243041;
}}
.ticket-row small{{
    color:#94a3b8;
}}
.ticket-row form{{
    margin:0;
}}
.ticket-message{{
    white-space:pre-wrap;
    background:#1e293b;
    border-radius:8px;
    padding:10px;
}}
@media(max-width:700px){{
    .ticket-row{{
        grid-template-columns:1fr;
    }}
}}
</style>
</head>
<body>
<main class="admin-shell">
    <div class="topbar">
        <div>
            <h1>Panel Admin</h1>
            <p>Sala administrada: anuncios</p>
        </div>
        <a href="/admin/logout">Cerrar sesión</a>
    </div>
    {mensaje_html}
    <section class="panel">
        <h2>Publicar anuncio en sala Anuncios</h2>
        <form method="post" action="/admin/dashboard">
            <textarea name="anuncio" placeholder="Este mensaje se publicara solo en la sala Anuncios..."></textarea>
            <button name="accion" value="publicar" type="submit">Publicar en Anuncios</button>
            <button class="danger" name="accion" value="limpiar_historial_anuncios" type="submit">
                Limpiar historial de anuncios
            </button>
        </form>
    </section>
    <section class="panel">
        <h2>Fijar mensaje por sala</h2>
        <p class="current">{fijado or "No hay mensaje fijado."}</p>
        <form method="post" action="/admin/dashboard">
            <select name="sala_fijado">
                <option value="general" {general_selected}>General</option>
                <option value="anuncios" {anuncios_selected}>Anuncios</option>
            </select>
            <textarea name="fijado" placeholder="Mensaje fijado visible solo en la sala seleccionada...">{fijado}</textarea>
            <button name="accion" value="fijar" type="submit">Fijar mensaje</button>
            <button class="danger" name="accion" value="limpiar" type="submit">Limpiar fijado</button>
        </form>
    </section>
    <section class="panel">
        <h2>Reiniciar chat del evento</h2>
        <p class="current">
            Borra historiales, salas de equipo y mensajes fijados.
            Conserva general, anuncios y soporte.
        </p>
        <form method="post" action="/admin/dashboard">
            <button class="danger" name="accion" value="reiniciar_chat" type="submit">
                Reiniciar mensajes y salas
            </button>
        </form>
    </section>
    <section class="panel">
        <h2>Tickets abiertos</h2>
        {tickets_html}
        {ticket_detalle_html}
    </section>
</main>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script>
var adminSocket = io();
adminSocket.emit("admin_conectar");
adminSocket.on("tickets_admin_actualizados", function(){{
    // El panel admin usa formularios simples; recargar mantiene la vista actualizada.
    window.location.reload();
}});
</script>
</body>
</html>
"""


def adminAutenticado():

    return session.get("admin_autenticado") is True


@app.route("/admin", methods=["GET", "POST"])
def adminLogin():

    if request.method == "POST":

        usuario = request.form.get(
            "usuario",
            ""
        )

        password = request.form.get(
            "password",
            ""
        )

        if (
            adminUser
            and adminPassword
            and usuario == adminUser
            and password == adminPassword
        ):

            session["admin_autenticado"] = True

            return redirect(
                url_for("adminDashboard")
            )

        return renderAdminLogin(
            "Credenciales invalidas."
        )

    if adminAutenticado():

        return redirect(
            url_for("adminDashboard")
        )

    return renderAdminLogin()


@app.route("/admin/dashboard", methods=["GET", "POST"])
def adminDashboard():

    if not adminAutenticado():

        return redirect(
            url_for("adminLogin")
        )

    mensaje_estado = ""
    ticket_seleccionado = request.values.get(
        "ticket",
        request.values.get(
            "ticket_id",
            ""
        )
    )
    sala_seleccionada = request.form.get(
        "sala_fijado",
        "anuncios"
    )

    if sala_seleccionada not in ("general", "anuncios"):

        sala_seleccionada = "anuncios"

    if request.method == "POST":

        accion = request.form.get(
            "accion",
            ""
        )

        if accion == "publicar":

            anuncio = request.form.get(
                "anuncio",
                ""
            ).strip()

            if anuncio:

                mensaje_anuncio = f"Admin: {anuncio}"

                guardarHistorialSala(
                    "anuncios",
                    mensaje_anuncio
                )

                socket.emit(
                    "message",
                    mensaje_anuncio,
                    to="anuncios"
                )

                mensaje_estado = "Anuncio publicado solo en la sala Anuncios."

        elif accion == "limpiar_historial_anuncios":

            historialSalas["anuncios"] = []

            socket.emit(
                "limpiar_historial_sala",
                "anuncios",
                to="anuncios"
            )

            mensaje_sistema = (
                "Sistema: El historial de anuncios fue limpiado por el administrador."
            )

            guardarHistorialSala(
                "anuncios",
                mensaje_sistema
            )

            socket.emit(
                "message",
                mensaje_sistema,
                to="anuncios"
            )

            mensaje_estado = "Historial de anuncios limpiado."

        elif accion == "fijar":

            fijado = request.form.get(
                "fijado",
                ""
            ).strip()

            mensajesFijados[sala_seleccionada] = fijado

            socket.emit(
                "mensaje_fijado",
                {
                    "sala": sala_seleccionada,
                    "texto": fijado
                },
                to=sala_seleccionada
            )

            mensaje_estado = "Mensaje fijado actualizado."

        elif accion == "limpiar":

            mensajesFijados[sala_seleccionada] = ""

            socket.emit(
                "mensaje_fijado",
                {
                    "sala": sala_seleccionada,
                    "texto": ""
                },
                to=sala_seleccionada
            )

            mensaje_estado = "Mensaje fijado limpiado."

        elif accion == "reiniciar_chat":

            reiniciarChatEvento()

            mensaje_estado = "Chat reiniciado para un nuevo evento."

        elif accion == "responder_ticket_admin":

            ticket_id = request.form.get(
                "ticket_id",
                ""
            )

            respuesta = request.form.get(
                "respuesta_admin",
                ""
            ).strip()

            ticket = tickets.get(ticket_id)

            if ticket and respuesta and ticket["estado"] in ("abierto", "bloqueado"):

                ticket["mensajes"].append({
                    "autor": "Admin",
                    "texto": respuesta
                })

                ticket["mensajes"] = ticket["mensajes"][-50:]

                emitirTicketPrivado(ticket_id)
                emitirTicketsUsuario(ticket["usuario"])

                mensaje_estado = "Respuesta enviada al ticket."
                ticket_seleccionado = ticket_id

        elif accion == "bloquear_chat":

            ticket_id = request.form.get(
                "ticket_id",
                ""
            )

            ticket = tickets.get(ticket_id)

            if ticket:

                ticket["estado"] = "bloqueado"

                ticket["mensajes"].append({
                    "autor": "Sistema",
                    "texto": "🔒 Soporte ha bloqueado la conversación.\n\nTu consulta fue atendida.\nSi necesitas ayuda adicional espera a que soporte reabra la conversación."
                })

                ticket["mensajes"] = ticket["mensajes"][-50:]

                emitirTicketPrivado(ticket_id)
                emitirTicketsUsuario(ticket["usuario"])

                mensaje_estado = "Chat de soporte bloqueado."
                ticket_seleccionado = ticket_id

        elif accion == "desbloquear_chat":

            ticket_id = request.form.get(
                "ticket_id",
                ""
            )

            ticket = tickets.get(ticket_id)

            if ticket:

                ticket["estado"] = "abierto"

                ticket["mensajes"].append({
                    "autor": "Sistema",
                    "texto": "🔓 Soporte ha reabierto la conversación."
                })

                ticket["mensajes"] = ticket["mensajes"][-50:]

                emitirTicketPrivado(ticket_id)
                emitirTicketsUsuario(ticket["usuario"])

                mensaje_estado = "Chat de soporte desbloqueado."
                ticket_seleccionado = ticket_id

    return renderAdminDashboard(
        mensaje_estado,
        sala_seleccionada,
        ticket_seleccionado
    )


@app.route("/admin/logout")
def adminLogout():

    session.pop(
        "admin_autenticado",
        None
    )

    return redirect(
        url_for("adminLogin")
    )


@app.route("/")
def index():

    return """
<!DOCTYPE html>
<html lang="es">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1.0">

<title>ByteChat</title>

<style>

/* =========================================
   VARIABLES
========================================= */

:root{

    --bg-main:#0f172a;
    --bg-sidebar:#111827;
    --bg-chat:#1e293b;

    --primary:#3b82f6;
    --secondary:#06b6d4;
    --accent:#22c55e;

    --text-main:#f8fafc;
    --text-muted:#94a3b8;

    --msg-sent:#2563eb;
    --msg-received:#334155;

    --border:#243041;
}

/* =========================================
   GLOBAL
========================================= */

*{
    margin:0;
    padding:0;
    box-sizing:border-box;
    font-family:Segoe UI, sans-serif;
}

body{
    background:linear-gradient(
        135deg,
        #0f172a,
        #111827
    );

    height:100vh;
    overflow:hidden;
}

/* =========================================
   APP
========================================= */

.chat-container{
    display:flex;
    height:100vh;
}

/* =========================================
   SIDEBAR
========================================= */

.sidebar{

    width:320px;

    background:rgba(17,24,39,.96);

    border-right:1px solid var(--border);

    display:flex;
    flex-direction:column;
    height:100vh;
    overflow-y:auto;

    backdrop-filter:blur(10px);
}

.mobile-topbar,
.sidebar-close,
.sidebar-backdrop,
.quick-commands{
    display:none;
}

.sidebar-section{
    border-bottom:1px solid var(--border);
}

.section-toggle{
    width:100%;
    border:none;
    background:transparent;
    color:#e5e7eb;
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:10px;
    padding:12px 18px;
    cursor:pointer;
    font-weight:800;
    text-transform:uppercase;
    letter-spacing:1px;
    font-size:13px;
}

.section-title{
    display:flex;
    align-items:center;
    gap:8px;
}

.section-arrow{
    color:var(--secondary);
    font-size:12px;
    width:14px;
}

.section-content{
    padding:0 18px 14px;
}

.sidebar-section.collapsed .section-content{
    display:none;
}

.sidebar-subtitle{
    color:#94a3b8;
    font-size:12px;
    margin:12px 0 8px;
    text-transform:uppercase;
    letter-spacing:.8px;
}

.rooms-panel .quick-commands{
    display:flex;
    flex-direction:column;
    gap:8px;
    margin-top:12px;
    padding-top:12px;
    border-top:1px solid var(--border);
}

.quick-command{
    border:1px solid var(--border);
    border-radius:10px;
    padding:9px 11px;
    background:#1e293b;
    color:white;
    text-align:left;
    cursor:pointer;
}

.rooms-panel{
    flex-shrink:0;
}

.rooms-panel h3{
    color:#94a3b8;
    font-size:13px;
    margin-bottom:12px;
    text-transform:uppercase;
    letter-spacing:1px;
}

.rooms-list{
    display:flex;
    flex-direction:column;
    gap:8px;
    margin-bottom:12px;
    max-height:230px;
    overflow-y:auto;
    padding-right:4px;
}

.room-item{
    border:1px solid var(--border);
    border-radius:10px;
    padding:9px 11px;
    background:#1e293b;
    color:white;
    text-align:left;
    cursor:pointer;
}

.room-item.active{
    border-color:var(--secondary);
    background:#0f3b57;
}

.room-create{
    display:flex;
    gap:8px;
}

.room-create input{
    min-width:0;
    flex:1;
    padding:10px;
    border:none;
    border-radius:10px;
    background:#1e293b;
    color:white;
    outline:none;
}

.room-create button{
    border:none;
    border-radius:10px;
    padding:0 12px;
    background:var(--secondary);
    color:white;
    cursor:pointer;
    font-weight:bold;
}

.support-panel{
    flex-shrink:0;
}

.sidebar .support-panel{
    display:none;
}

.support-panel h3{
    color:#94a3b8;
    font-size:13px;
    margin-bottom:10px;
    text-transform:uppercase;
    letter-spacing:1px;
}

.support-panel input,
.support-panel textarea{
    width:100%;
    margin-bottom:8px;
    padding:10px;
    border:none;
    border-radius:10px;
    background:#1e293b;
    color:white;
    outline:none;
    box-sizing:border-box;
}

.support-panel textarea{
    min-height:70px;
    resize:vertical;
}

.support-panel button{
    border:none;
    border-radius:10px;
    padding:9px 11px;
    margin:4px 4px 4px 0;
    background:var(--secondary);
    color:white;
    cursor:pointer;
    font-weight:700;
}

.support-actions{
    display:flex;
    flex-direction:column;
    gap:8px;
}

.ticket-list-item{
    width:100%;
    text-align:left;
    background:#1e293b;
    border:1px solid var(--border);
    border-radius:10px;
    padding:10px 11px;
    color:#e5e7eb;
    margin-bottom:6px;
}

#misTickets{
    max-height:170px;
    overflow-y:auto;
    padding-right:4px;
}

.ticket-conversation{
    max-height:180px;
    overflow-y:auto;
    border:1px solid var(--border);
    border-radius:10px;
    padding:8px;
    margin:8px 0;
}

.ticket-conversation p{
    color:#e5e7eb;
    font-size:13px;
    margin-bottom:7px;
    white-space:pre-wrap;
}

/* =========================================
   BRAND
========================================= */

.brand{

    padding:25px;

    border-bottom:1px solid var(--border);

    text-align:center;
}

.brand h1{

    color:white;

    font-size:34px;

    font-weight:800;

    letter-spacing:1px;
}

.brand span{
    color:var(--secondary);
}

/* =========================================
   LOGIN
========================================= */

.login-box{

    padding:20px;

    border-bottom:1px solid var(--border);

    display:flex;
    flex-direction:column;

    gap:12px;
}

.login-box input{

    padding:14px;

    border:none;

    border-radius:12px;

    background:#1e293b;

    color:white;

    outline:none;

    font-size:15px;
}

.login-box input::placeholder{
    color:#64748b;
}

.btn-primary{

    background:linear-gradient(
        135deg,
        var(--primary),
        var(--secondary)
    );

    color:white;

    border:none;

    padding:14px;

    border-radius:12px;

    cursor:pointer;

    font-weight:bold;

    transition:.2s;
}

.btn-primary:hover{

    transform:translateY(-2px);

    box-shadow:0 0 15px rgba(59,130,246,.4);
}

/* =========================================
   USERS
========================================= */

.users-list{

    flex-shrink:0;

    overflow:hidden;

    padding:0;
}

.users-list .section-content{
    max-height:220px;
    overflow-y:auto;
    padding-right:12px;
    padding-left:18px;
}

.users-list h3{

    color:#94a3b8;

    font-size:13px;

    margin-bottom:15px;

    text-transform:uppercase;

    letter-spacing:1px;
}

.user-item{

    display:flex;

    align-items:center;

    gap:12px;

    padding:12px;

    border-radius:14px;

    margin-bottom:8px;

    transition:.2s;
}

.user-item:hover{

    background:#1e293b;
}

.user-item.active{

    background:#1e293b;
}

.avatar{

    width:42px;
    height:42px;

    border-radius:50%;

    display:flex;

    align-items:center;

    justify-content:center;

    font-weight:bold;

    color:white;

    background:linear-gradient(
        135deg,
        var(--primary),
        var(--secondary)
    );
}

.user-info{

    display:flex;
    flex-direction:column;
}

.user-name{

    color:white;

    font-size:14px;

    font-weight:600;
}

.user-status{

    color:var(--accent);

    font-size:12px;
}

/* =========================================
   MAIN
========================================= */

.chat-main{

    flex:1;

    display:flex;

    flex-direction:column;

    background:#0f172a;
}

/* =========================================
   HEADER
========================================= */

.chat-header{

    padding:20px;

    border-bottom:1px solid var(--border);

    background:rgba(15,23,42,.95);

    backdrop-filter:blur(10px);
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:16px;
    position:relative;
    z-index:40;
}

.chat-header-title{
    min-width:0;
}

.chat-header h2{

    color:white;

    margin-bottom:4px;
}

.chat-header p{

    color:#94a3b8;

    font-size:13px;
}

.header-actions{
    position:relative;
    display:flex;
    align-items:center;
    gap:8px;
    flex-shrink:0;
    z-index:50;
}

.header-icon-btn{
    position:relative;
    width:38px;
    height:38px;
    border:1px solid var(--border);
    border-radius:11px;
    background:#1e293b;
    color:#e5e7eb;
    cursor:pointer;
    font-size:18px;
    display:flex;
    align-items:center;
    justify-content:center;
    transition:.2s;
}

.header-icon-btn:hover,
.header-icon-btn.active{
    border-color:var(--secondary);
    color:white;
    background:#0f3b57;
}

.notification-badge{
    display:none;
    position:absolute;
    top:-5px;
    right:-5px;
    min-width:17px;
    height:17px;
    padding:0 4px;
    border-radius:999px;
    background:#ef4444;
    color:white;
    font-size:10px;
    line-height:17px;
    font-weight:800;
}

.notification-badge.show{
    display:block;
}

.header-search{
    display:none;
    width:190px;
    border:1px solid var(--border);
    border-radius:11px;
    padding:10px 12px;
    background:#1e293b;
    color:white;
    outline:none;
}

.header-search.show{
    display:block;
}

.header-dropdown{
    display:none;
    position:absolute;
    top:46px;
    right:0;
    width:min(300px, 82vw);
    padding:12px;
    border:1px solid var(--border);
    border-radius:12px;
    background:#111827;
    box-shadow:0 18px 45px rgba(0,0,0,.35);
    z-index:90;
}

.header-dropdown.show{
    display:block;
}

.header-dropdown h3{
    color:white;
    font-size:14px;
    margin-bottom:8px;
}

.header-dropdown p,
.header-dropdown li{
    color:#cbd5e1;
    font-size:13px;
    line-height:1.45;
}

.header-dropdown ul{
    list-style:none;
    display:flex;
    flex-direction:column;
    gap:8px;
    padding:0;
    margin:0;
}

.header-menu-item{
    border:none;
    width:100%;
    padding:9px 10px;
    border-radius:9px;
    background:#1e293b;
    color:#e5e7eb;
    text-align:left;
    cursor:pointer;
}

.header-menu-item:hover{
    background:#0f3b57;
    color:white;
}

.message.search-hidden,
.msg-system.search-hidden{
    display:none;
}

/* =========================================
   CHAT
========================================= */

.chat-messages{

    flex:1;

    overflow-y:auto;

    padding:20px;

    display:flex;

    flex-direction:column;

    gap:16px;
    position:relative;
    z-index:1;
}

.pinned-message{
    display:none;
    margin:14px 20px 0;
    padding:12px 14px;
    border:1px solid #facc15;
    border-radius:10px;
    background:rgba(250,204,21,.12);
    color:#fef3c7;
    white-space:pre-wrap;
}

.pinned-message.show{
    display:block;
}

/* =========================================
   SYSTEM
========================================= */

.msg-system{

    text-align:center;
}

.msg-system span{

    background:#1e293b;

    color:#cbd5e1;

    padding:6px 14px;

    border-radius:20px;

    font-size:12px;
}

/* =========================================
   MESSAGE
========================================= */

.message{

    display:flex;

    flex-direction:column;

    max-width:75%;
}

.msg-meta{

    color:#94a3b8;

    font-size:12px;

    margin-bottom:4px;

    font-weight:600;
}

.msg-bubble{

    padding:12px 14px;

    border-radius:16px;

    position:relative;

    box-shadow:0 2px 8px rgba(0,0,0,.15);
}

.msg-bubble p{

    color:white;

    font-size:15px;

    line-height:1.4;

    white-space:pre-wrap;
}

.msg-bubble a{

    color:#bfdbfe;

    font-weight:700;
}

.msg-time{

    display:block;

    margin-top:5px;

    font-size:10px;

    opacity:.7;

    text-align:right;

    color:white;
}

/* =========================================
   RECEIVED
========================================= */

.msg-received{

    align-self:flex-start;
}

.msg-received .msg-bubble{

    background:var(--msg-received);

    border-top-left-radius:4px;
}

/* =========================================
   SENT
========================================= */

.msg-sent{

    align-self:flex-end;
}

.msg-sent .msg-meta{

    text-align:right;
}

.msg-sent .msg-bubble{

    background:linear-gradient(
        135deg,
        var(--primary),
        var(--secondary)
    );

    border-top-right-radius:4px;
}

/* =========================================
   TELEGRAM TAG
========================================= */

.tag-bot{

    background:#229ED9;

    color:white;

    padding:2px 6px;

    border-radius:6px;

    font-size:10px;

    margin-left:5px;
}

/* =========================================
   FOOTER
========================================= */

.chat-footer{

    padding:18px;

    border-top:1px solid var(--border);

    background:#111827;
}

.message-form{

    display:flex;

    gap:12px;
}

.message-form input{

    flex:1;

    padding:14px;

    border:none;

    border-radius:14px;

    background:#1e293b;

    color:white;

    outline:none;

    font-size:14px;
}

.message-form input::placeholder{

    color:#64748b;
}

.btn-send{

    width:50px;
    height:50px;

    border:none;

    border-radius:50%;

    background:linear-gradient(
        135deg,
        var(--primary),
        var(--secondary)
    );

    color:white;

    cursor:pointer;

    transition:.2s;
}

.btn-send:hover{

    transform:scale(1.05);

    box-shadow:0 0 20px rgba(59,130,246,.4);
}

.message-form input:disabled,
.btn-send:disabled{
    opacity:.55;
    cursor:not-allowed;
}

.btn-send:disabled:hover{
    transform:none;
    box-shadow:none;
}

/* =========================================
   SCROLL
========================================= */

::-webkit-scrollbar{
    width:6px;
}

::-webkit-scrollbar-thumb{
    background:#334155;
    border-radius:20px;
}

/* =========================================
   RESPONSIVE
========================================= */

@media(max-width:768px){

    html,
    body{
        height:100dvh;
        height:var(--app-height, 100dvh);
        width:100%;
        max-width:100vw;
        overflow:hidden;
    }

    .chat-container{
        flex-direction:column;
        height:100dvh;
        height:var(--app-height, 100dvh);
        width:100%;
        max-width:100vw;
        overflow:hidden;
    }

    .mobile-topbar{
        display:flex;
        align-items:center;
        gap:12px;
        height:56px;
        padding:10px 14px;
        flex-shrink:0;
        border-bottom:1px solid var(--border);
        background:rgba(15,23,42,.96);
        width:100%;
        max-width:100vw;
        box-sizing:border-box;
        position:relative;
        z-index:40;
    }

    .menu-toggle,
    .sidebar-close{
        width:40px;
        height:40px;
        border:none;
        border-radius:10px;
        background:#1e293b;
        color:white;
        font-size:22px;
        cursor:pointer;
    }

    .mobile-title{
        display:flex;
        flex-direction:column;
        min-width:0;
        flex:1;
    }

    .mobile-title strong{
        color:white;
        font-size:19px;
        line-height:1.1;
    }

    .mobile-title strong span{
        color:var(--secondary);
    }

    .mobile-title span{
        color:#94a3b8;
        font-size:12px;
    }

    .sidebar{

        position:fixed;
        top:0;
        bottom:0;
        left:0;
        z-index:90;
        width:min(84vw, 320px);
        height:100dvh;
        height:var(--app-height, 100dvh);
        max-height:100dvh;
        max-height:var(--app-height, 100dvh);
        flex-shrink:0;
        overflow-y:auto;
        padding-bottom:calc(88px + env(safe-area-inset-bottom));
        box-sizing:border-box;
        transform:translateX(-100%);
        transition:transform .2s ease;
        box-shadow:18px 0 40px rgba(0,0,0,.35);
    }

    .sidebar.open{
        transform:translateX(0);
    }

    .sidebar-backdrop{
        display:none;
        position:fixed;
        inset:0;
        z-index:80;
        background:rgba(2,6,23,.55);
    }

    .sidebar-backdrop.open{
        display:block;
    }

    .sidebar-close{
        display:flex;
        align-items:center;
        justify-content:center;
        position:absolute;
        top:12px;
        right:12px;
        z-index:95;
        width:38px;
        height:38px;
        border:1px solid var(--border);
        border-radius:10px;
        background:#1e293b;
        color:white;
        font-size:24px;
        line-height:1;
        cursor:pointer;
    }

    .brand{
        padding:18px 56px 18px 18px;
    }

    .login-box{
        padding:12px;
        gap:8px;
    }

    .login-box input{
        padding:11px 12px;
        font-size:14px;
    }

    .btn-primary{
        padding:11px;
    }

    .section-toggle{
        padding:11px 12px;
        font-size:12px;
    }

    .section-content{
        padding:0 12px 12px;
    }

    .rooms-list{
        gap:6px;
        margin-bottom:10px;
        max-height:155px;
    }

    .room-item{
        padding:8px 10px;
    }

    .users-list{

        flex:none;
        max-height:none;
        padding:0;
        overflow:hidden;
    }

    .users-list .section-content{
        max-height:155px;
        padding:0 12px 12px;
        overflow-y:auto;
    }

    .support-panel .section-content{
        max-height:none;
        overflow:visible;
    }

    .user-item{
        padding:8px;
        margin-bottom:5px;
    }

    .avatar{
        width:34px;
        height:34px;
    }

    .quick-commands{
        display:flex;
        flex-direction:column;
        gap:8px;
    }

    .quick-commands h3{
        color:#94a3b8;
        font-size:12px;
        text-transform:uppercase;
        letter-spacing:1px;
    }

    .quick-command{
        border:1px solid var(--border);
        border-radius:10px;
        padding:9px 11px;
        background:#1e293b;
        color:white;
        text-align:left;
        cursor:pointer;
    }

    #misTickets{
        max-height:130px;
    }

    .ticket-conversation{
        max-height:140px;
    }

    .chat-main{
        flex:1;
        min-height:0;
        height:auto;
        display:flex;
        flex-direction:column;
        width:100%;
        max-width:100vw;
        overflow:hidden;
    }

    .chat-header{
        display:none;
    }

    .chat-header-title{
        display:none;
    }

    .header-actions{
        width:auto;
        max-width:none;
        margin-left:auto;
        justify-content:flex-end;
        gap:6px;
        flex-wrap:nowrap;
        box-sizing:border-box;
    }

    .header-icon-btn{
        width:34px;
        height:34px;
        font-size:15px;
        border-radius:10px;
    }

    .header-search{
        position:fixed;
        top:62px;
        left:12px;
        right:12px;
        width:auto;
        max-width:calc(100vw - 24px);
        padding:9px 10px;
        box-sizing:border-box;
        z-index:70;
    }

    .header-dropdown{
        position:fixed;
        top:62px;
        right:12px;
        width:auto;
        max-width:calc(100vw - 24px);
        box-sizing:border-box;
        z-index:70;
    }

    .chat-messages{
        flex:1;
        min-height:0;
        overflow-y:auto;
        padding:12px;
        gap:10px;
    }

    .pinned-message{
        margin:10px 12px 0;
        padding:10px 12px;
        font-size:14px;
    }

    .chat-footer{
        flex-shrink:0;
        padding:10px 12px;
        padding-bottom:calc(20px + env(safe-area-inset-bottom));
        box-sizing:border-box;
    }

    .message-form{
        gap:8px;
    }

    .message-form input{
        min-width:0;
        padding:12px;
    }

    .btn-send{
        width:46px;
        height:46px;
        flex-shrink:0;
    }

    .message{
        max-width:90%;
        overflow-wrap:anywhere;
    }

    .brand h1{
        font-size:28px;
    }
}

</style>

</head>

<body>

<div class="chat-container">

    <div class="mobile-topbar">

        <button
            type="button"
            class="menu-toggle"
            onclick="toggleMenuMovil()"
            aria-label="Abrir menu"
            aria-expanded="false"
        >
            ☰
        </button>

        <div class="mobile-title">
            <strong>Byte<span>Chat</span></strong>
            <span id="salaMovil">Sala General</span>
        </div>

    </div>

    <!-- SIDEBAR -->

    <aside class="sidebar">

        <button
            type="button"
            class="sidebar-close"
            onclick="cerrarMenuMovil()"
            aria-label="Cerrar menú"
        >
            ×
        </button>

        <div class="brand">

            <h1>Byte<span>Chat</span></h1>

        </div>

        <div class="login-box">

            <input
                type="text"
                id="nombre"
                placeholder="Tu alias competitivo..."
            >

            <button
                id="btn-entrar"
                class="btn-primary"
                onclick="guardarNombre()"
            >
                Entrar al Chat
            </button>

        </div>

        <div class="sidebar-section rooms-panel">

            <button
                type="button"
                class="section-toggle"
                onclick="toggleSidebarSection(this)"
            >
                <span class="section-title">
                    <span class="section-arrow">▼</span>
                    Canales
                </span>
            </button>

            <div class="section-content">

                <div
                    id="listaSalas"
                    class="rooms-list"
                ></div>

                <form
                    class="room-create"
                    onsubmit="event.preventDefault(); crearSalaEquipo();"
                >
                    <input
                        type="text"
                        id="nombreSalaEquipo"
                        placeholder="Nombre del equipo"
                    >

                    <button type="submit">+</button>
                </form>

                <div class="quick-commands">

                    <h3>Comandos rapidos</h3>

                    <button
                        type="button"
                        class="quick-command"
                        onclick="enviarComandoRapido('/ayuda')"
                    >
                        /ayuda
                    </button>

                    <button
                        type="button"
                        class="quick-command"
                        onclick="enviarComandoRapido('/ubicacion')"
                    >
                        /ubicacion
                    </button>

                    <button
                        type="button"
                        class="quick-command"
                        onclick="enviarComandoRapido('/teambook')"
                    >
                        /teambook
                    </button>

                </div>

            </div>

        </div>

        <div class="sidebar-section support-panel">

            <button
                type="button"
                class="section-toggle"
                onclick="toggleSidebarSection(this)"
            >
                <span class="section-title">
                    <span class="section-arrow">▼</span>
                    Soporte
                </span>
            </button>

            <div class="section-content">

                <p
                    id="ticketEstado"
                    class="user-status"
                >
                    Sin ticket abierto.
                </p>

                <button
                    type="button"
                    id="btnCrearTicket"
                    onclick="mostrarFormularioTicket()"
                >
                    Crear ticket de soporte
                </button>

                <button
                    type="button"
                    id="btnAbrirTicket"
                    style="display:none;"
                    onclick="abrirMiTicketAbierto()"
                >
                    Abrir mi ticket
                </button>

                <div id="ticketForm" style="display:none;">
                    <input
                        type="text"
                        id="ticketAsunto"
                        placeholder="Asunto"
                    >
                    <textarea
                        id="ticketMensaje"
                        placeholder="Mensaje inicial"
                    ></textarea>
                    <button type="button" onclick="crearTicketSoporte()">
                        Enviar ticket
                    </button>
                </div>

                <div id="misTickets" style="display:none;"></div>

                <div id="ticketDetalle" style="display:none;">
                    <h3 id="ticketTitulo"></h3>
                    <div
                        id="ticketConversacion"
                        class="ticket-conversation"
                    ></div>
                    <textarea
                        id="ticketRespuesta"
                        placeholder="Responder ticket"
                    ></textarea>
                    <button type="button" onclick="responderTicketUsuario()">
                        Responder
                    </button>
                </div>

            </div>

        </div>

        <div class="sidebar-section users-list">

            <button
                type="button"
                class="section-toggle"
                onclick="toggleSidebarSection(this)"
            >
                <span class="section-title">
                    <span class="section-arrow">▼</span>
                    Conectados
                </span>
            </button>

            <div class="section-content">
                <div id="listaUsuarios"></div>
            </div>

        </div>

    </aside>

    <div
        class="sidebar-backdrop"
        onclick="cerrarMenuMovil()"
    ></div>

    <!-- CHAT -->

    <main class="chat-main">

        <header class="chat-header">

            <div class="chat-header-title">

                <h2>ByteChat Arena</h2>

                <p>
                    <span id="salaActualTitulo">General</span>
                    &bull;
                    <span id="salaActualSubtitulo">
                        Programacion Competitiva
                    </span>
                </p>

            </div>

            <div class="header-actions">

                <input
                    type="search"
                    id="busquedaMensajes"
                    class="header-search"
                    placeholder="Buscar mensajes..."
                    oninput="filtrarMensajesVisibles()"
                >

                <button
                    type="button"
                    class="header-icon-btn"
                    id="btnBuscarHeader"
                    aria-label="Buscar mensajes"
                    onclick="toggleBusquedaHeader()"
                >
                    &#128269;
                </button>

                <button
                    type="button"
                    class="header-icon-btn"
                    id="btnNotificaciones"
                    aria-label="Abrir notificaciones"
                    onclick="toggleDropdownHeader('notificacionesPanel')"
                >
                    &#128276;
                    <span
                        class="notification-badge"
                        id="notificacionesBadge"
                    >0</span>
                </button>

                <button
                    type="button"
                    class="header-icon-btn"
                    id="btnMenuHeader"
                    aria-label="Abrir menu de informacion"
                    onclick="toggleDropdownHeader('menuHeaderPanel')"
                >
                    &#8942;
                </button>

                <div
                    class="header-dropdown"
                    id="notificacionesPanel"
                >
                    <h3>Notificaciones</h3>
                    <ul id="listaNotificaciones">
                        <li>No hay notificaciones nuevas</li>
                    </ul>
                </div>

                <div
                    class="header-dropdown"
                    id="menuHeaderPanel"
                >
                    <button
                        type="button"
                        class="header-menu-item"
                        onclick="mostrarInfoHeader('acerca')"
                    >
                        Acerca de ByteChat
                    </button>
                    <button
                        type="button"
                        class="header-menu-item"
                        onclick="mostrarInfoHeader('desarrollado')"
                    >
                        Desarrollado por
                    </button>
                    <button
                        type="button"
                        class="header-menu-item"
                        onclick="mostrarInfoHeader('ayuda')"
                    >
                        Ayuda
                    </button>
                    <p id="menuHeaderInfo">
                        ByteChat Arena - Plataforma de comunicacion para eventos de programacion competitiva.
                    </p>
                </div>

            </div>

        </header>

        <!-- MENSAJES -->

        <div
            class="pinned-message"
            id="mensajeFijado"
        ></div>

        <div
            class="chat-messages"
            id="chat"
        ></div>

        <!-- FOOTER -->

        <footer class="chat-footer">

            <form
                class="message-form"
                onsubmit="event.preventDefault(); enviar();"
            >

                <input
                    type="text"
                    id="mensaje"
                    placeholder="Escribe tu mensaje..."
                >

                <button
                    type="submit"
                    class="btn-send"
                >
                    ➤
                </button>

            </form>

        </footer>

    </main>

</div>

<!-- SOCKET -->

<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>

<script>

var socket = io();

var nombre = "";
var salaActual = "general";
var salasDisponibles = [
    "general",
    "anuncios",
    "soporte",
    "ia"
];
var mensajesFijados = {
    general:"",
    anuncios:"",
    soporte:"",
    ia:""
};
var ticketsUsuario = [];
var ticketActual = "";
var notificacionesHeader = [];
var soporteBloqueado = false;

// =========================================
// ALTURA MOVIL SEGURA
// =========================================

function setAppHeight(){

    const height =
        window.visualViewport
        ? window.visualViewport.height
        : window.innerHeight;

    document.documentElement.style.setProperty(
        "--app-height",
        height + "px"
    );
}

function ubicarAccionesHeader(){

    let acciones =
        document.querySelector(".header-actions");

    let topbar =
        document.querySelector(".mobile-topbar");

    let header =
        document.querySelector(".chat-header");

    let tituloHeader =
        document.querySelector(".chat-header-title");

    if(!acciones || !topbar || !header){
        return;
    }

    if(window.innerWidth <= 768){

        if(acciones.parentElement !== topbar){
            topbar.appendChild(acciones);
        }

    }else if(acciones.parentElement !== header){

        header.appendChild(acciones);

        if(tituloHeader){
            header.insertBefore(acciones, tituloHeader.nextSibling);
        }
    }
}

window.addEventListener(
    "resize",
    function(){
        setAppHeight();
        ubicarAccionesHeader();
    }
);

if(window.visualViewport){

    window.visualViewport.addEventListener(
        "resize",
        setAppHeight
    );
}

setAppHeight();
ubicarAccionesHeader();

// =========================================
// MENU MOVIL
// =========================================

function actualizarEstadoMenuMovil(abierto){

    let boton =
        document.querySelector(".menu-toggle");

    if(boton){
        boton.setAttribute(
            "aria-expanded",
            abierto ? "true" : "false"
        );
    }
}

function abrirMenuMovil(){

    cerrarDropdownsHeader();
    cerrarBusquedaHeader();

    document
        .querySelector(".sidebar")
        .classList.add("open");

    document
        .querySelector(".sidebar-backdrop")
        .classList.add("open");

    actualizarEstadoMenuMovil(true);
}

function cerrarMenuMovil(){

    document
        .querySelector(".sidebar")
        .classList.remove("open");

    document
        .querySelector(".sidebar-backdrop")
        .classList.remove("open");

    actualizarEstadoMenuMovil(false);
}

function toggleMenuMovil(){

    let sidebar =
        document.querySelector(".sidebar");

    if(sidebar.classList.contains("open")){

        cerrarMenuMovil();

    }else{

        abrirMenuMovil();
    }
}

function enviarComandoRapido(comando){

    document.getElementById("mensaje").value =
        comando;

    cerrarMenuMovil();
    enviar();
}

function cerrarDropdownsHeader(){

    document
        .querySelectorAll(".header-dropdown")
        .forEach(function(panel){
            panel.classList.remove("show");
        });
}

function cerrarBusquedaHeader(){

    let input =
        document.getElementById("busquedaMensajes");

    if(!input){
        return;
    }

    input.value = "";
    input.classList.remove("show");
    filtrarMensajesVisibles();
}

function toggleDropdownHeader(id){

    let panel =
        document.getElementById(id);

    let estabaAbierto =
        panel.classList.contains("show");

    cerrarDropdownsHeader();

    if(!estabaAbierto){
        panel.classList.add("show");
    }
}

function toggleBusquedaHeader(){

    let input =
        document.getElementById("busquedaMensajes");

    input.classList.toggle("show");

    if(input.classList.contains("show")){

        input.focus();

    }else{

        input.value = "";
        filtrarMensajesVisibles();
    }

    cerrarDropdownsHeader();
}

function filtrarMensajesVisibles(){

    let input =
        document.getElementById("busquedaMensajes");

    let termino =
        input.value.trim().toLowerCase();

    document
        .querySelectorAll("#chat .message, #chat .msg-system")
        .forEach(function(item){

            let coincide =
                !termino ||
                item.textContent.toLowerCase().includes(termino);

            item.classList.toggle(
                "search-hidden",
                !coincide
            );
        });
}

function actualizarNotificacionesHeader(){

    let listas =
        document.querySelectorAll("#listaNotificaciones");

    let badges =
        document.querySelectorAll("#notificacionesBadge");

    let noLeidas =
        notificacionesHeader.filter(function(notificacion){
            return !notificacion.leida;
        });

    if(noLeidas.length == 0){

        listas.forEach(function(lista){
            lista.innerHTML =
                "<li>No hay notificaciones nuevas.</li>";
        });

        badges.forEach(function(badge){
            badge.textContent = "0";
            badge.classList.remove("show");
        });
        return;
    }

    listas.forEach(function(lista){

        lista.innerHTML = "";

        noLeidas
            .slice(-5)
            .reverse()
            .forEach(function(notificacion){

                let item =
                    document.createElement("button");

                item.type = "button";
                item.className = "header-menu-item";
                item.textContent =
                    notificacion.texto;

                item.onclick = function(){
                    abrirNotificacionTicket(
                        notificacion.id
                    );
                };

                lista.appendChild(item);
            });
    });

    badges.forEach(function(badge){
        badge.textContent =
            String(noLeidas.length);

        badge.classList.add("show");
    });
}

function agregarNotificacionTicket(ticket){

    console.log(
        "notificacion recibida",
        {
            ticket_id:ticket.id,
            cantidad:notificacionesHeader.length
        }
    );

    let mensajes =
        ticket.mensajes || [];

    let ultimo =
        mensajes.length
        ? mensajes[mensajes.length - 1]
        : null;

    let usuarioTicket =
        (ticket.usuario || "").trim().toLowerCase();

    let usuarioActual =
        (nombre || "").trim().toLowerCase();

    if(
        !ultimo ||
        ultimo.autor != "Admin" ||
        (
            usuarioTicket &&
            usuarioActual &&
            usuarioTicket != usuarioActual
        )
    ){
        console.log(
            "notificacion ignorada",
            {
                ticket_id:ticket.id
            }
        );
        return;
    }

    let idNotificacion =
        ticket.id + "-" + mensajes.length;

    let existe =
        notificacionesHeader.some(function(notificacion){
            return notificacion.id == idNotificacion;
        });

    if(existe){
        return;
    }

    notificacionesHeader.push({
        id:idNotificacion,
        ticket_id:ticket.id,
        leida:false,
        texto:"Respuesta de soporte: " + ultimo.texto
    });

    notificacionesHeader =
        notificacionesHeader.slice(-9);

    console.log(
        "notificacion registrada",
        {
            ticket_id:ticket.id,
            cantidad:notificacionesHeader.filter(function(notificacion){
                return !notificacion.leida;
            }).length
        }
    );

    actualizarNotificacionesHeader();
}

function abrirNotificacionTicket(idNotificacion){

    let notificacion =
        notificacionesHeader.find(function(item){
            return item.id == idNotificacion;
        });

    if(!notificacion){
        return;
    }

    notificacion.leida = true;
    actualizarNotificacionesHeader();

    if(salaActual == "soporte"){

        socket.emit(
            "cambiar_sala",
            "soporte"
        );

    }else{

        cambiarSala("soporte");
    }

    cerrarDropdownsHeader();
}

function mostrarInfoHeader(tipo){

    let info =
        document.getElementById("menuHeaderInfo");

    if(tipo == "desarrollado"){

        info.textContent =
            "Desarrollado por Rafael Stoessel y equipo.";

    }else if(tipo == "ayuda"){

        info.textContent =
            "Usa los canales, tickets y comandos rapidos desde la barra lateral. En la sala IA puedes consultar el TeamBook.";

    }else{

        info.textContent =
            "ByteChat Arena - Plataforma de comunicacion para eventos de programacion competitiva.";
    }
}

function toggleSidebarSection(boton){

    let section =
        boton.closest(".sidebar-section");

    if(!section){
        return;
    }

    section.classList.toggle("collapsed");

    let arrow =
        section.querySelector(".section-arrow");

    if(arrow){
        arrow.textContent =
            section.classList.contains("collapsed")
            ? "▶"
            : "▼";
    }
}

// =========================================
// SALAS
// =========================================

function nombreSalaLegible(sala){

    if(sala == "general"){
        return "General";
    }

    if(sala == "anuncios"){
        return "Anuncios";
    }

    if(sala == "soporte"){
        return "Soporte";
    }

    if(sala == "ia"){
        return "IA";
    }

    return sala.replace(/-/g, " ");
}

function actualizarSalaActual(){

    let etiqueta =
        nombreSalaLegible(salaActual);

    document.getElementById("salaActualTitulo").textContent =
        etiqueta;

    document.getElementById("salaActualSubtitulo").textContent =
        salaActual == "soporte"
        ? "Conversacion privada con soporte"
        : "Sala " + etiqueta;

    document.getElementById("salaMovil").textContent =
        salaActual == "soporte"
        ? "Soporte privado"
        : "Sala " + etiqueta;

    actualizarMensajeFijado();
    actualizarBloqueoSoporte();
}

function actualizarBloqueoSoporte(){

    let input =
        document.getElementById("mensaje");

    let boton =
        document.querySelector(".btn-send");

    let bloqueado =
        salaActual == "soporte" && soporteBloqueado;

    input.disabled =
        bloqueado;

    boton.disabled =
        bloqueado;

    if(bloqueado){

        input.placeholder =
            "Chat bloqueado por soporte";

    }else{

        input.placeholder =
            "Escribe tu mensaje...";
    }
}

function actualizarMensajeFijado(){

    let caja =
        document.getElementById("mensajeFijado");

    let texto =
        mensajesFijados[salaActual] || "";

    if(texto){

        caja.textContent =
            texto;

        caja.classList.add("show");

    }else{

        caja.textContent = "";
        caja.classList.remove("show");
    }
}

function actualizarSalas(salas){

    salasDisponibles = salas;

    let lista =
        document.getElementById("listaSalas");

    lista.innerHTML = "";

    salas.forEach(function(sala){

        let boton =
            document.createElement("button");

        boton.type = "button";
        boton.className = "room-item";

        if(sala == salaActual){
            boton.classList.add("active");
        }

        boton.textContent =
            nombreSalaLegible(sala);

        boton.onclick = function(){
            cambiarSala(sala);
        };

        lista.appendChild(boton);
    });
}

function cambiarSala(sala){

    if(sala == salaActual){
        cerrarBusquedaHeader();
        cerrarMenuMovil();
        return;
    }

    socket.emit(
        "cambiar_sala",
        sala
    );
}

function crearSalaEquipo(){

    let input =
        document.getElementById("nombreSalaEquipo");

    let equipo =
        input.value.trim();

    if(equipo == ""){
        return;
    }

    socket.emit(
        "crear_sala",
        equipo
    );

    input.value = "";
}

// =========================================
// TICKETS DE SOPORTE
// =========================================

function mostrarFormularioTicket(){

    let ticketAbierto =
        obtenerTicketAbierto();

    if(ticketAbierto){
        abrirTicket(ticketAbierto.id);
        return;
    }

    let form =
        document.getElementById("ticketForm");

    form.style.display =
        form.style.display == "none"
        ? "block"
        : "none";
}

function crearTicketSoporte(){

    if(nombre == ""){
        alert("Ingrese nombre");
        return;
    }

    if(obtenerTicketAbierto()){
        alert("Ya tienes un ticket abierto.");
        return;
    }

    let asunto =
        document.getElementById("ticketAsunto").value.trim();

    let mensaje =
        document.getElementById("ticketMensaje").value.trim();

    if(asunto == "" || mensaje == ""){
        return;
    }

    socket.emit(
        "crear_ticket",
        {
            asunto:asunto,
            mensaje:mensaje
        }
    );

    document.getElementById("ticketAsunto").value = "";
    document.getElementById("ticketMensaje").value = "";
    document.getElementById("ticketForm").style.display = "none";
}

function obtenerTicketAbierto(){

    return ticketsUsuario.find(function(ticket){
        return ticket.estado == "abierto";
    });
}

function abrirMiTicketAbierto(){

    let ticketAbierto =
        obtenerTicketAbierto();

    if(ticketAbierto){
        abrirTicket(ticketAbierto.id);
    }
}

function actualizarEstadoTicketSoporte(){

    let ticketAbierto =
        obtenerTicketAbierto();

    let estado =
        document.getElementById("ticketEstado");

    let btnCrear =
        document.getElementById("btnCrearTicket");

    let btnAbrir =
        document.getElementById("btnAbrirTicket");

    let form =
        document.getElementById("ticketForm");

    if(ticketAbierto){

        estado.textContent =
            "Ticket abierto: " + ticketAbierto.id + " (" + ticketAbierto.estado + ")";

        btnCrear.style.display = "none";
        btnAbrir.style.display = "inline-block";
        form.style.display = "none";

    }else{

        estado.textContent =
            "Sin ticket abierto.";

        btnCrear.style.display = "inline-block";
        btnAbrir.style.display = "none";
    }
}

function actualizarMisTickets(tickets){

    ticketsUsuario =
        tickets || [];

    let lista =
        document.getElementById("misTickets");

    lista.innerHTML = "";
    actualizarEstadoTicketSoporte();

    if(ticketsUsuario.length == 0){
        lista.innerHTML = "<p class='user-status'>Sin tickets.</p>";
        return;
    }

    ticketsUsuario.forEach(function(ticket){

        let boton =
            document.createElement("button");

        boton.type = "button";
        boton.className = "ticket-list-item";
        boton.textContent =
            ticket.id + " - " + ticket.asunto + " (" + ticket.estado + ")";

        boton.onclick = function(){
            abrirTicket(ticket.id);
        };

        lista.appendChild(boton);
    });
}

function abrirTicket(ticketId){

    ticketActual =
        ticketId;

    socket.emit(
        "abrir_ticket",
        ticketId
    );
}

function mostrarTicket(ticket){

    ticketActual =
        ticket.id;

    let indiceTicket =
        ticketsUsuario.findIndex(function(item){
            return item.id == ticket.id;
        });

    if(indiceTicket >= 0){

        ticketsUsuario[indiceTicket] = ticket;

    }else{

        ticketsUsuario.push(ticket);
    }

    actualizarEstadoTicketSoporte();

    document.getElementById("ticketDetalle").style.display =
        "block";

    document.getElementById("ticketTitulo").textContent =
        ticket.id + " - " + ticket.asunto + " (" + ticket.estado + ")";

    let conversacion =
        document.getElementById("ticketConversacion");

    conversacion.innerHTML = "";

    ticket.mensajes.forEach(function(mensaje){

        let p =
            document.createElement("p");

        p.innerHTML =
            "<strong>" +
            escaparHTML(mensaje.autor) +
            ":</strong> " +
            escaparHTML(mensaje.texto);

        conversacion.appendChild(p);
    });

    document.getElementById("ticketRespuesta").disabled =
        ticket.estado != "abierto";
}

function responderTicketUsuario(){

    let texto =
        document.getElementById("ticketRespuesta").value.trim();

    if(ticketActual == "" || texto == ""){
        return;
    }

    socket.emit(
        "responder_ticket_usuario",
        {
            ticket_id:ticketActual,
            mensaje:texto
        }
    );

    document.getElementById("ticketRespuesta").value = "";
}

// =========================================
// GUARDAR NOMBRE
// =========================================

function guardarNombre(){

    let input =
        document.getElementById("nombre");

    if(input.value.trim() == ""){

        alert("Ingrese un nombre");

        return;
    }

    nombre = input.value.trim();
    input.value = nombre;

    input.disabled = true;

    document.getElementById(
        "btn-entrar"
    ).disabled = true;

    agregarSistema(
        nombre + " se unió al chat"
    );

    agregarUsuario(
        nombre,
        true,
        false
    );

    socket.emit(
        "registrar_usuario",
        nombre
    );

    socket.emit(
        "listar_mis_tickets"
    );
}

// =========================================
// AGREGAR USUARIO
// =========================================

function agregarUsuario(
    usuario,
    activo=false,
    telegram=false
){

    let lista =
        document.getElementById(
            "listaUsuarios"
        );

    let div =
        document.createElement("div");

    div.className =
        "user-item";

    if(activo){
        div.classList.add("active");
    }

    let inicial =
        usuario.charAt(0).toUpperCase();

    let usuarioSeguro =
        escaparHTML(usuario);

    let inicialSegura =
        escaparHTML(inicial);

    let estado =
        telegram
        ? "vía Telegram"
        : "En línea";

    div.innerHTML =

        '<div class="avatar">' +
            inicialSegura +
        '</div>' +

        '<div class="user-info">' +

            '<span class="user-name">' +
                usuarioSeguro +
            '</span>' +

            '<span class="user-status">' +
                estado +
            '</span>' +

        '</div>';

    lista.appendChild(div);
}

function actualizarUsuarios(usuarios){

    let lista =
        document.getElementById(
            "listaUsuarios"
        );

    lista.innerHTML = "";

    usuarios.forEach(function(usuario){

        agregarUsuario(
            usuario.nombre,
            usuario.nombre == nombre && !usuario.telegram,
            usuario.telegram
        );
    });
}

// =========================================
// ENVIAR
// =========================================

function enviar(){

    if(salaActual == "soporte" && soporteBloqueado){
        return;
    }

    let mensajeInput =
        document.getElementById(
            "mensaje"
        );

    let mensaje =
        mensajeInput.value;

    if(nombre == ""){

        alert("Ingrese nombre");

        return;
    }

    if(mensaje.trim() == ""){

        return;
    }

    socket.emit(
        "chat_message",
        {
            usuario:nombre,
            mensaje:mensaje,
            sala:salaActual
        }
    );

    mensajeInput.value = "";
}

// =========================================
// RECIBIR WEB
// =========================================

socket.on("message", function(msg){

    agregarMensaje(
        msg,
        false
    );
});

// =========================================
// RECIBIR TELEGRAM
// =========================================

socket.on("telegram_message", function(msg){

    agregarMensaje(
        msg,
        true
    );
});

socket.on("usuarios", function(usuarios){

    actualizarUsuarios(
        usuarios
    );
});

socket.on("salas", function(salas){

    actualizarSalas(
        salas
    );
});

socket.on("sala_actual", function(sala){

    salaActual = sala;
    cerrarBusquedaHeader();
    actualizarSalaActual();
    actualizarSalas(salasDisponibles);

    cerrarMenuMovil();
});

socket.on("mensaje_fijado", function(data){

    let sala =
        "anuncios";

    let texto =
        "";

    if(typeof data == "string"){

        texto = data;

    }else if(data){

        sala = data.sala || "anuncios";
        texto = data.texto || "";
    }

    mensajesFijados[sala] =
        texto;

    actualizarMensajeFijado();
});

socket.on("historial_sala", function(data){

    let mensajes =
        data;

    let salaHistorial =
        "";

    if(data && !Array.isArray(data)){

        mensajes = data.mensajes || [];
        salaHistorial = data.sala || "";
    }

    if(!Array.isArray(mensajes)){
        return;
    }

    document.getElementById("chat").innerHTML = "";

    if(salaHistorial == "soporte" && mensajes.length == 0){

        agregarMensaje(
            "Soporte ByteChat: Conversacion privada con soporte. Escribe tu mensaje y un administrador respondera.",
            false
        );

        return;
    }

    mensajes.forEach(function(msg){

        agregarMensaje(
            msg,
            false
        );
    });

    filtrarMensajesVisibles();
});

socket.on("chat_reiniciado", function(data){

    let salas =
        data && data.salas
        ? data.salas
        : [
            "general",
            "anuncios",
            "soporte",
            "ia"
        ];

    salasDisponibles =
        salas;

    if(!salas.includes(salaActual)){

        salaActual =
            "general";
    }

    mensajesFijados = {
        general:"",
        anuncios:"",
        soporte:"",
        ia:""
    };

    actualizarSalaActual();
    actualizarSalas(salasDisponibles);

    document.getElementById("chat").innerHTML = "";

    agregarSistema(
        data && data.mensaje
        ? data.mensaje
        : "El chat fue reiniciado por el administrador."
    );
});

socket.on("limpiar_historial_sala", function(sala){

    if(sala == salaActual){

        document.getElementById("chat").innerHTML = "";
    }
});

socket.on("tickets_usuario", function(tickets){

    actualizarMisTickets(
        tickets
    );
});

socket.on("ticket_detalle", function(ticket){

    mostrarTicket(
        ticket
    );
});

socket.on("ticket_actualizado", function(ticket){

    socket.emit(
        "listar_mis_tickets"
    );

    agregarNotificacionTicket(ticket);

    if(ticketActual == ticket.id){

        mostrarTicket(
            ticket
        );
    }
});

socket.on("soporte_estado", function(data){

    soporteBloqueado =
        !!(data && data.bloqueado);

    actualizarBloqueoSoporte();
});

socket.on("ticket_error", function(mensaje){

    alert(mensaje);
});

socket.on("system_message", function(msg){

    agregarSistema(
        msg
    );
});

// =========================================
// SISTEMA
// =========================================

function agregarSistema(texto){

    let chat =
        document.getElementById("chat");

    let div =
        document.createElement("div");

    div.className =
        "msg-system";

    div.innerHTML =
        "<span>📢 Sistema: " +
        escaparHTML(texto) +
        "</span>";

    chat.appendChild(div);

    chat.scrollTop =
        chat.scrollHeight;
}

// =========================================
// FORMATO SEGURO DE TEXTO
// =========================================

function escaparHTML(texto){

    return texto
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function formatearTextoMensaje(texto){

    let seguro =
        escaparHTML(texto);

    return seguro.replace(
        /(https?:\/\/[^\s<]+|\/static\/docs\/[^\s<]+)/g,
        function(url){

            return '<a href="' + url + '" target="_blank" rel="noopener">' +
                url +
            '</a>';
        }
    );
}

// =========================================
// MENSAJES
// =========================================

function agregarMensaje(
    msg,
    telegram=false
){

    let chat =
        document.getElementById("chat");

    let div =
        document.createElement("div");

    div.classList.add("message");

    let partes =
        msg.split(":");

    let usuario =
        partes[0];

    let texto =
        partes.slice(1).join(":");

    let hora =
        new Date()
        .toLocaleTimeString(
            [],
            {
                hour:'2-digit',
                minute:'2-digit'
            }
        );

    if(usuario == nombre){

        div.classList.add(
            "msg-sent"
        );

    }else{

        div.classList.add(
            "msg-received"
        );
    }

    let tagTelegram = "";

    if(telegram){

        tagTelegram =
            '<span class="tag-bot">' +
            'Telegram' +
            '</span>';

    }

    div.innerHTML =

        '<div class="msg-meta">' +

            usuario +

            tagTelegram +

        '</div>' +

        '<div class="msg-bubble">' +

            '<p>' +
                formatearTextoMensaje(texto) +
            '</p>' +

            '<span class="msg-time">' +
                hora +
            '</span>' +

        '</div>';

    chat.appendChild(div);

    chat.scrollTop =
        chat.scrollHeight;
}

// =========================================
// ENTER
// =========================================

document
.getElementById("mensaje")
.addEventListener(
    "keypress",
    function(e){

        if(e.key === "Enter"){

            enviar();
        }
    }
);

document.addEventListener(
    "click",
    function(event){

        if(!event.target.closest(".header-actions")){
            cerrarDropdownsHeader();
        }
    }
);

</script>

</body>
</html>
"""


# =========================================
# USUARIOS SOCKET
# =========================================

@socket.on("connect")
def conectarUsuario():

    salasUsuario[request.sid] = "general"
    join_room("general")
    emitirSalas(request.sid)
    socket.emit(
        "sala_actual",
        "general",
        to=request.sid
    )
    emitirEstadoSala(
        "general",
        request.sid
    )
    emitirUsuarios()


@socket.on("registrar_usuario")
def registrarUsuario(nombre):

    nombre = str(nombre).strip()

    if not nombre:

        return

    usuariosWeb[request.sid] = nombre

    socket.emit(
        "system_message",
        f"{nombre} se unio al chat",
        skip_sid=request.sid
    )

    emitirEstadoSala(
        salasUsuario.get(
            request.sid,
            "general"
        ),
        request.sid
    )

    emitirUsuarios()
    emitirTicketsUsuario(nombre)


@socket.on("admin_conectar")
def adminConectar():

    join_room("admins")


@socket.on("disconnect")
def desconectarUsuario():

    sala = salasUsuario.pop(
        request.sid,
        "general"
    )

    leave_room(sala)

    nombre = usuariosWeb.pop(
        request.sid,
        None
    )

    if not nombre:

        return

    socket.emit(
        "system_message",
        f"{nombre} salio del chat"
    )

    emitirUsuarios()


# =========================================
# SALAS SOCKET
# =========================================

@socket.on("cambiar_sala")
def cambiarSala(sala):

    sala = str(sala).strip()

    if sala not in obtenerSalas():

        return

    sala_anterior = salasUsuario.get(
        request.sid,
        "general"
    )

    if sala_anterior != sala:

        leave_room(sala_anterior)
        join_room(sala)
        salasUsuario[request.sid] = sala

    socket.emit(
        "sala_actual",
        sala,
        to=request.sid
    )

    emitirEstadoSala(
        sala,
        request.sid
    )


@socket.on("crear_sala")
def crearSala(nombre):

    sala = normalizarSala(nombre)

    if not sala:

        return

    if sala not in salasBase and sala not in salasDinamicas:

        salasDinamicas.append(sala)
        historialSalas[sala] = []
        mensajesFijados[sala] = ""
        emitirSalas()

    cambiarSala(sala)


# =========================================
# TICKETS SOCKET
# =========================================

@socket.on("crear_ticket")
def crearTicket(data):

    if not isinstance(data, dict):

        return

    usuario = usuariosWeb.get(
        request.sid,
        ""
    )

    if not usuario:

        socket.emit(
            "ticket_error",
            "Debes ingresar con un alias antes de crear tickets.",
            to=request.sid
        )

        return

    ticket_abierto = obtenerTicketAbiertoUsuario(
        usuario
    )

    if ticket_abierto:

        socket.emit(
            "ticket_error",
            "Ya tienes un ticket de soporte abierto.",
            to=request.sid
        )

        socket.emit(
            "ticket_detalle",
            serializarTicket(ticket_abierto),
            to=request.sid
        )

        emitirTicketsUsuario(usuario)
        return

    asunto = str(data.get("asunto", "")).strip()
    mensaje = str(data.get("mensaje", "")).strip()

    if not asunto or not mensaje:

        return

    ticket_id = crearTicketId()

    tickets[ticket_id] = {
        "id": ticket_id,
        "usuario": usuario,
        "asunto": asunto[:80],
        "estado": "abierto",
        "mensajes": [
            {
                "autor": usuario,
                "texto": mensaje
            }
        ]
    }

    join_room(
        f"ticket-{ticket_id}"
    )

    emitirTicketsUsuario(usuario)
    emitirTicketPrivado(ticket_id)


@socket.on("listar_mis_tickets")
def listarMisTickets():

    usuario = usuariosWeb.get(
        request.sid,
        ""
    )

    socket.emit(
        "tickets_usuario",
        obtenerTicketsUsuario(usuario),
        to=request.sid
    )


@socket.on("abrir_ticket")
def abrirTicket(ticket_id):

    ticket_id = str(ticket_id).strip()
    ticket = tickets.get(ticket_id)
    usuario = usuariosWeb.get(
        request.sid,
        ""
    )

    if not ticket or ticket["usuario"] != usuario:

        socket.emit(
            "ticket_error",
            "No puedes abrir ese ticket.",
            to=request.sid
        )

        return

    join_room(
        f"ticket-{ticket_id}"
    )

    socket.emit(
        "ticket_detalle",
        serializarTicket(ticket),
        to=request.sid
    )


@socket.on("responder_ticket_usuario")
def responderTicketUsuario(data):

    if not isinstance(data, dict):

        return

    ticket_id = str(data.get("ticket_id", "")).strip()
    texto = str(data.get("mensaje", "")).strip()
    ticket = tickets.get(ticket_id)
    usuario = usuariosWeb.get(
        request.sid,
        ""
    )

    if (
        not ticket
        or ticket["usuario"] != usuario
        or ticket["estado"] != "abierto"
        or not texto
    ):

        socket.emit(
            "ticket_error",
            "No se pudo responder el ticket.",
            to=request.sid
        )

        return

    ticket["mensajes"].append({
        "autor": usuario,
        "texto": texto
    })

    ticket["mensajes"] = ticket["mensajes"][-50:]

    join_room(
        f"ticket-{ticket_id}"
    )

    emitirTicketPrivado(ticket_id)
    emitirTicketsUsuario(usuario)


def recibirMensajeSoporte(usuario, texto):

    ticket = asegurarTicketSoporteUsuario(
        usuario
    )

    if ticket["estado"] == "bloqueado":

        socket.emit(
            "soporte_estado",
            {
                "bloqueado": True
            },
            to=request.sid
        )

        return

    ticket["mensajes"].append({
        "autor": usuario,
        "texto": texto
    })

    ticket["mensajes"] = ticket["mensajes"][-50:]

    join_room(
        f"ticket-{ticket['id']}"
    )

    socket.emit(
        "historial_sala",
        {
            "sala": "soporte",
            "mensajes": serializarMensajesSoporte(ticket)
        },
        to=request.sid
    )

    socket.emit(
        "tickets_admin_actualizados",
        True,
        to="admins"
    )

    emitirTicketsUsuario(usuario)


# =========================================
# MENSAJES WEB
# =========================================

@socket.on("chat_message")
def recibirMensajeSala(data):

    if not isinstance(data, dict):

        return

    usuario = str(data.get("usuario", "")).strip()
    texto_usuario = str(data.get("mensaje", "")).strip()
    sala = str(data.get("sala", "general")).strip()

    if not usuario or not texto_usuario:

        return

    if sala not in obtenerSalas():

        sala = salasUsuario.get(
            request.sid,
            "general"
        )

    if sala != salasUsuario.get(request.sid):

        cambiarSala(sala)

    if sala == "anuncios":

        socket.emit(
            "message",
            "ByteBot: Solo el administrador puede publicar en la sala de anuncios.",
            to=request.sid
        )

        return

    if sala == "soporte":

        recibirMensajeSoporte(
            usuario,
            texto_usuario
        )

        return

    mensaje = f"{usuario}: {texto_usuario}"

    print("Mensaje WEB:", mensaje, "Sala:", sala)

    guardarHistorialSala(
        sala,
        mensaje
    )

    socket.emit(
        "message",
        mensaje,
        to=sala
    )

    if sala == "ia":

        mensaje_pensando = "ByteBot IA: esta pensando..."

        socket.emit(
            "message",
            mensaje_pensando,
            to=sala
        )

        threading.Thread(
            target=responderIAEnSegundoPlano,
            args=(texto_usuario,),
            daemon=True
        ).start()

        return

    respuesta = get_command_response(
        texto_usuario.strip().lower()
    )

    if respuesta:

        mensaje_bot = f"ByteBot: {respuesta['text']}"

        guardarHistorialSala(
            sala,
            mensaje_bot
        )

        socket.emit(
            "message",
            mensaje_bot,
            to=sala
        )

    if sala == "general":

        threading.Thread(
            target=enviarTelegram,
            args=(mensaje,),
            daemon=True
        ).start()


def responderIAEnSegundoPlano(pregunta):

    respuesta = preguntar_ia(
        pregunta
    )

    mensaje_ia = f"ByteBot IA: {respuesta}"

    guardarHistorialSala(
        "ia",
        mensaje_ia
    )

    socket.emit(
        "message",
        mensaje_ia,
        to="ia"
    )


@socket.on("message")
def recibirMensaje(mensaje):

    print("Mensaje WEB:", mensaje)

    # Compatibilidad con clientes antiguos: el canal historico publica en general.
    guardarHistorialSala(
        "general",
        mensaje
    )

    send(
        mensaje,
        to="general"
    )

    # El cliente envia el formato "usuario: texto". Se toma solo el texto
    # y se compara completo para evitar activar comandos dentro de una frase.
    _, separador, textoUsuario = mensaje.partition(":")

    if separador:

        comando = textoUsuario.strip().lower()
        respuesta = get_command_response(comando)

        if respuesta:

            mensaje_bot = f"ByteBot: {respuesta['text']}"

            guardarHistorialSala(
                "general",
                mensaje_bot
            )

            # ByteBot responde directamente en el chat web mediante SocketIO.
            # Esta respuesta no pasa por Telegram.
            send(
                mensaje_bot,
                to="general"
            )

    # Telegram se conserva como canal de notificaciones para mensajes web en general.
    threading.Thread(
        target=enviarTelegram,
        args=(mensaje,),
        daemon=True
    ).start()


# =========================================
# ENVIAR TELEGRAM
# =========================================

def enviarTelegram(mensaje):

    # Sin credenciales, el chat web sigue funcionando y se omite la notificacion.
    if not tokenTelegram or not chatID:

        print(
            "Notificacion Telegram omitida: "
            "configure TOKEN_TELEGRAM y CHAT_ID."
        )

        return

    url = (
        f"https://api.telegram.org/bot"
        f"{tokenTelegram}/sendMessage"
    )

    data = {
        "chat_id": chatID,
        "text": mensaje
    }

    try:

        respuesta = requests.post(
            url,
            data=data,
            timeout=10
        )

        respuesta.raise_for_status()

    except Exception as e:

        print("Error Telegram:", e)


# =========================================
# RECIBIR TELEGRAM
# =========================================

async def recibirTelegram(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global chatID

    if not update.message or not update.message.text:

        return

    if not chatID and update.effective_chat:

        chatID = str(update.effective_chat.id)

        print(
            "CHAT_ID detectado para pruebas locales:",
            chatID
        )

    usuario = (
        update.message
        .from_user
        .first_name
    )

    registrarUsuarioTelegram(
        usuario
    )

    mensaje = update.message.text

    texto =f"{usuario}: {mensaje}"

    print("Telegram:", texto)

    guardarHistorialSala(
        "general",
        texto
    )

    socket.emit(
        "telegram_message",
        texto,
        to="general"
    )

    respuesta = get_command_response(mensaje)

    if not respuesta:

        return

    mensaje_bot = f"ByteBot: {respuesta['text']}"

    guardarHistorialSala(
        "general",
        mensaje_bot
    )

    socket.emit(
        "telegram_message",
        mensaje_bot,
        to="general"
    )

    if (
        respuesta["command"] == "/teambook"
        and respuesta["document_path"]
    ):

        with open(respuesta["document_path"], "rb") as documento:

            await update.message.reply_document(
                document=documento,
                filename=TEAMBOOK_FILENAME,
                caption=respuesta["text"]
            )

        return

    await update.message.reply_text(
        respuesta["text"]
    )


# =========================================
# INICIAR TELEGRAM
# =========================================

def ejecutarBotTelegram():

    # python-telegram-bot necesita su propio event loop dentro de este hilo.
    asyncio.set_event_loop(
        asyncio.new_event_loop()
    )

    try:

        print("BOT TELEGRAM ACTIVO")

        # En un hilo secundario no se deben registrar senales del sistema.
        botTelegram.run_polling(
            stop_signals=None
        )

    except Exception as e:

        # Un fallo de Telegram no debe detener Flask ni el chat web.
        print("Error al iniciar Telegram:", e)


def iniciarBotTelegram():

    global botTelegram

    if not tokenTelegram:

        print(
            "Bot Telegram desactivado: "
            "configure TOKEN_TELEGRAM."
        )

        return

    botTelegram = ApplicationBuilder().token(
        tokenTelegram
    ).build()

    botTelegram.add_handler(

        MessageHandler(
            filters.TEXT,
            recibirTelegram
        )
    )

    threading.Thread(
        target=ejecutarBotTelegram,
        daemon=True,
        name="telegram-polling"
    ).start()


# Gunicorn importa ByteChat:app y no ejecuta el bloque __main__.
# Por eso Telegram debe iniciarse al cargar el modulo.
iniciarBotTelegram()


# =========================================
# MAIN LOCAL
# =========================================

if __name__ == "__main__":

    puerto = int(
        os.environ.get(
            "PORT",
            "5000"
        )
    )

    print(f"Servidor iniciado en el puerto {puerto}")

    # En Render el servidor de produccion es Gunicorn. Este bloque queda
    # disponible para ejecutar localmente con: python ByteChat.py
    socket.run(
        app,
        host="0.0.0.0",
        port=puerto,
        debug=False,
        allow_unsafe_werkzeug=True
    )
