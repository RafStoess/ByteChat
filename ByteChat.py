from flask import Flask, request
from flask_socketio import SocketIO, send, join_room, leave_room
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes
)

import asyncio
import os
import threading
import requests


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


# =========================================
# APP
# =========================================

app = Flask(__name__)

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
    "soporte"
]
salasDinamicas = []
salasUsuario = {}


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
        "\U0001f4cb BYTECHAT - AYUDA\n\n"
        "Informacion\n"
        "/info\n"
        "/inscripcion\n"
        "/requisitos\n"
        "/ubicacion\n\n"
        "Competencia\n"
        "/reglas\n"
        "/cronograma\n"
        "/horarios\n"
        "/algoritmos\n\n"
        "Recursos\n"
        "/teambook\n\n"
        "Contacto\n"
        "/contacto\n\n"
        "Escribe cualquiera de los comandos para obtener mas informacion."
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
        "BFS recorre un grafo por niveles utilizando una cola.\n\n"
        "Uso comun:\n"
        "- Caminos minimos en grafos no ponderados\n"
        "- Busqueda por niveles\n"
        "- Problemas de estados\n\n"
        "Estructura general:\n"
        "1. Crear una cola.\n"
        "2. Marcar el nodo inicial como visitado.\n"
        "3. Procesar nodos mientras la cola no este vacia.\n"
        "4. Agregar vecinos no visitados."
    ),
    "/dfs": (
        "\U0001f332 DFS - Depth First Search\n\n"
        "DFS recorre un grafo profundizando por cada rama antes de retroceder.\n\n"
        "Uso comun:\n"
        "- Componentes conectados\n"
        "- Deteccion de ciclos\n"
        "- Backtracking\n"
        "- Ordenamiento topologico\n\n"
        "Estructura general:\n"
        "1. Marcar el nodo actual.\n"
        "2. Recorrer sus vecinos.\n"
        "3. Llamar recursivamente a los no visitados."
    ),
    "/dijkstra": (
        "\U0001f6e3\ufe0f DIJKSTRA\n\n"
        "Dijkstra encuentra caminos minimos en grafos con pesos no negativos.\n\n"
        "Uso comun:\n"
        "- Rutas minimas\n"
        "- Grafos ponderados\n"
        "- Optimizacion de costos\n\n"
        "Estructura general:\n"
        "1. Inicializar distancias en infinito.\n"
        "2. Usar una cola de prioridad.\n"
        "3. Relajar aristas.\n"
        "4. Actualizar distancias minimas."
    ),
    "/greedy": (
        "\u26a1 GREEDY\n\n"
        "Greedy toma la mejor decision local en cada paso esperando llegar "
        "a una solucion optima.\n\n"
        "Uso comun:\n"
        "- Intervalos\n"
        "- Seleccion de actividades\n"
        "- Huffman\n"
        "- Problemas de ordenamiento\n\n"
        "Estructura general:\n"
        "1. Ordenar o priorizar elementos.\n"
        "2. Elegir la mejor opcion actual.\n"
        "3. Verificar si la eleccion mantiene la solucion valida."
    ),
    "/dp": (
        "\U0001f9e9 PROGRAMACION DINAMICA\n\n"
        "La Programacion Dinamica resuelve problemas dividiendolos en "
        "subproblemas y reutilizando resultados.\n\n"
        "Uso comun:\n"
        "- Mochila\n"
        "- Caminos\n"
        "- Subsecuencias\n"
        "- Optimizacion\n\n"
        "Estructura general:\n"
        "1. Definir estado.\n"
        "2. Definir transicion.\n"
        "3. Definir casos base.\n"
        "4. Calcular la respuesta."
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
        "Backtracking explora soluciones posibles y retrocede cuando una "
        "opcion ya no puede llevar a una respuesta valida.\n\n"
        "Uso comun:\n"
        "- Permutaciones\n"
        "- Combinaciones\n"
        "- Sudoku\n"
        "- Busqueda exhaustiva con poda\n\n"
        "Estructura general:\n"
        "1. Elegir una opcion.\n"
        "2. Avanzar recursivamente.\n"
        "3. Deshacer la eleccion.\n"
        "4. Podar estados invalidos."
    )
}


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

    backdrop-filter:blur(10px);
}

.mobile-topbar,
.sidebar-close,
.sidebar-backdrop,
.quick-commands{
    display:none;
}

.rooms-panel{
    padding:16px 18px;
    border-bottom:1px solid var(--border);
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

    flex:1;

    overflow-y:auto;

    padding:18px;
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
}

.chat-header h2{

    color:white;

    margin-bottom:4px;
}

.chat-header p{

    color:#94a3b8;

    font-size:13px;
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

    body{
        height:100dvh;
        height:var(--app-height, 100dvh);
        overflow:hidden;
    }

    .chat-container{
        flex-direction:column;
        height:100dvh;
        height:var(--app-height, 100dvh);
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
        z-index:30;
        width:min(84vw, 320px);
        height:100dvh;
        height:var(--app-height, 100dvh);
        max-height:none;
        flex-shrink:0;
        overflow-y:auto;
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
        z-index:20;
        background:rgba(2,6,23,.55);
    }

    .sidebar-backdrop.open{
        display:block;
    }

    .sidebar-close{
        display:block;
        position:absolute;
        top:12px;
        right:12px;
        font-size:24px;
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

    .rooms-panel{
        padding:12px;
    }

    .rooms-panel h3{
        font-size:12px;
        margin-bottom:8px;
    }

    .rooms-list{
        gap:6px;
        margin-bottom:10px;
    }

    .room-item{
        padding:8px 10px;
    }

    .users-list{

        flex:none;
        max-height:220px;
        padding:10px 12px;
        overflow-y:auto;
    }

    .users-list h3{
        font-size:12px;
        margin-bottom:8px;
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
        padding:12px;
        border-top:1px solid var(--border);
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
        padding:10px 12px;
        background:#1e293b;
        color:white;
        text-align:left;
        cursor:pointer;
    }

    .chat-main{
        flex:1;
        min-height:0;
        height:auto;
        display:flex;
        flex-direction:column;
    }

    .chat-header{
        padding:12px;
        flex-shrink:0;
    }

    .chat-messages{
        flex:1;
        min-height:0;
        overflow-y:auto;
        padding:12px;
        gap:10px;
    }

    .chat-footer{
        flex-shrink:0;
        padding:10px 12px;
        padding-bottom:calc(12px + env(safe-area-inset-bottom));
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
            onclick="abrirMenuMovil()"
            aria-label="Abrir menu"
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
            aria-label="Cerrar menu"
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

        <div class="rooms-panel">

            <h3>Salas</h3>

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

        </div>

        <div class="users-list">

            <h3>Conectados</h3>

            <div id="listaUsuarios"></div>

        </div>

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

    </aside>

    <div
        class="sidebar-backdrop"
        onclick="cerrarMenuMovil()"
    ></div>

    <!-- CHAT -->

    <main class="chat-main">

        <header class="chat-header">

            <h2>ByteChat Arena</h2>

            <p>
                <span id="salaActualTitulo">General</span>
                &bull;
                <span id="salaActualSubtitulo">
                    Programacion Competitiva
                </span>
            </p>

        </header>

        <!-- MENSAJES -->

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
    "soporte"
];

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

window.addEventListener(
    "resize",
    setAppHeight
);

if(window.visualViewport){

    window.visualViewport.addEventListener(
        "resize",
        setAppHeight
    );
}

setAppHeight();

// =========================================
// MENU MOVIL
// =========================================

function abrirMenuMovil(){

    document
        .querySelector(".sidebar")
        .classList.add("open");

    document
        .querySelector(".sidebar-backdrop")
        .classList.add("open");
}

function cerrarMenuMovil(){

    document
        .querySelector(".sidebar")
        .classList.remove("open");

    document
        .querySelector(".sidebar-backdrop")
        .classList.remove("open");
}

function enviarComandoRapido(comando){

    document.getElementById("mensaje").value =
        comando;

    cerrarMenuMovil();
    enviar();
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

    return sala.replace(/-/g, " ");
}

function actualizarSalaActual(){

    let etiqueta =
        nombreSalaLegible(salaActual);

    document.getElementById("salaActualTitulo").textContent =
        etiqueta;

    document.getElementById("salaActualSubtitulo").textContent =
        "Sala " + etiqueta;

    document.getElementById("salaMovil").textContent =
        "Sala " + etiqueta;
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
// GUARDAR NOMBRE
// =========================================

function guardarNombre(){

    let input =
        document.getElementById("nombre");

    if(input.value.trim() == ""){

        alert("Ingrese un nombre");

        return;
    }

    nombre = input.value;

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
    actualizarSalaActual();
    actualizarSalas(salasDisponibles);

    document.getElementById("chat").innerHTML = "";

    agregarSistema(
        "Entraste a la sala " + nombreSalaLegible(sala)
    );

    cerrarMenuMovil();
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

    emitirUsuarios()


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


@socket.on("crear_sala")
def crearSala(nombre):

    sala = normalizarSala(nombre)

    if not sala:

        return

    if sala not in salasBase and sala not in salasDinamicas:

        salasDinamicas.append(sala)
        emitirSalas()

    cambiarSala(sala)


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

    mensaje = f"{usuario}: {texto_usuario}"

    print("Mensaje WEB:", mensaje, "Sala:", sala)

    socket.emit(
        "message",
        mensaje,
        to=sala
    )

    respuesta = get_command_response(
        texto_usuario.strip().lower()
    )

    if respuesta:

        socket.emit(
            "message",
            f"ByteBot: {respuesta['text']}",
            to=sala
        )

    if sala == "general":

        threading.Thread(
            target=enviarTelegram,
            args=(mensaje,),
            daemon=True
        ).start()


@socket.on("message")
def recibirMensaje(mensaje):

    print("Mensaje WEB:", mensaje)

    # Compatibilidad con clientes antiguos: el canal historico publica en general.
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

            # ByteBot responde directamente en el chat web mediante SocketIO.
            # Esta respuesta no pasa por Telegram.
            send(
                f"ByteBot: {respuesta['text']}",
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

    socket.emit(
        "telegram_message",
        texto,
        to="general"
    )

    respuesta = get_command_response(mensaje)

    if not respuesta:

        return

    socket.emit(
        "telegram_message",
        f"ByteBot: {respuesta['text']}",
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
