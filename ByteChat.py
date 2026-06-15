from flask import Flask
from flask_socketio import SocketIO, send
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


# =========================================
# RESPUESTAS AUTOMATICAS DE BYTEBOT
# =========================================

# Cada clave es un comando exacto aceptado por el chat web.
respuestasBot = {
    "/ayuda": (
        "Comandos disponibles: /cpp, /python, /complejidad, /greedy, "
        "/dp, /bfs, /dfs, /dijkstra y /estructuras."
    ),
    "/cpp": (
        "C++ es muy usado en programacion competitiva por su velocidad "
        "y por la biblioteca STL, que incluye vectores, colas, mapas y algoritmos."
    ),
    "/python": (
        "Python permite escribir soluciones rapidamente. Usa sys.stdin.readline "
        "para entradas grandes y cuida el rendimiento en ciclos intensivos."
    ),
    "/complejidad": (
        "La complejidad mide como crecen el tiempo y la memoria de un algoritmo. "
        "Por ejemplo, O(n) es lineal y O(n log n) suele ser eficiente."
    ),
    "/greedy": (
        "Un algoritmo greedy elige la mejor opcion local en cada paso. "
        "Funciona solo cuando esas decisiones conducen a una solucion optima global."
    ),
    "/dp": (
        "La programacion dinamica resuelve subproblemas repetidos y guarda sus "
        "resultados mediante memoizacion o tabulacion."
    ),
    "/bfs": (
        "BFS (Breadth First Search) recorre un grafo por niveles usando una cola. "
        "Complejidad O(V+E)."
    ),
    "/dfs": (
        "DFS (Depth First Search) explora un camino en profundidad usando recursion "
        "o una pila. Complejidad O(V+E)."
    ),
    "/dijkstra": (
        "Dijkstra encuentra caminos minimos desde un origen en grafos con pesos "
        "no negativos, normalmente usando una cola de prioridad."
    ),
    "/estructuras": (
        "Las estructuras mas comunes son arreglos, pilas, colas, conjuntos, mapas, "
        "heaps y grafos. Elige segun las operaciones que necesites optimizar."
    )
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

    .chat-container{
        flex-direction:column;
    }

    .sidebar{

        width:100%;
        height:auto;
    }

    .users-list{

        max-height:180px;
    }

    .chat-main{
        height:100%;
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

    <!-- SIDEBAR -->

    <aside class="sidebar">

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

        <div class="users-list">

            <h3>Conectados</h3>

            <div id="listaUsuarios"></div>

        </div>

    </aside>

    <!-- CHAT -->

    <main class="chat-main">

        <header class="chat-header">

            <h2>ByteChat Arena</h2>

            <p>
                Sala Global • Programación Competitiva
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

    let estado =
        telegram
        ? "vía Telegram"
        : "En línea";

    div.innerHTML =

        '<div class="avatar">' +
            inicial +
        '</div>' +

        '<div class="user-info">' +

            '<span class="user-name">' +
                usuario +
            '</span>' +

            '<span class="user-status">' +
                estado +
            '</span>' +

        '</div>';

    lista.appendChild(div);
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

    socket.send(
        nombre + ": " + mensaje
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
        texto +
        "</span>";

    chat.appendChild(div);

    chat.scrollTop =
        chat.scrollHeight;
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

        agregarUsuario(
            usuario,
            false,
            true
        );
    }

    div.innerHTML =

        '<div class="msg-meta">' +

            usuario +

            tagTelegram +

        '</div>' +

        '<div class="msg-bubble">' +

            '<p>' +
                texto +
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
# MENSAJES WEB
# =========================================

@socket.on("message")
def recibirMensaje(mensaje):

    print("Mensaje WEB:", mensaje)

    # Mantiene la publicacion normal del mensaje escrito por el usuario.
    send(mensaje, broadcast=True)

    # El cliente envia el formato "usuario: texto". Se toma solo el texto
    # y se compara completo para evitar activar comandos dentro de una frase.
    _, separador, textoUsuario = mensaje.partition(":")

    if separador:

        comando = textoUsuario.strip().lower()
        respuesta = respuestasBot.get(comando)

        if respuesta:

            # ByteBot responde directamente en el chat web mediante SocketIO.
            # Esta respuesta no pasa por Telegram.
            send(
                f"ByteBot: {respuesta}",
                broadcast=True
            )

    # Telegram se conserva como canal de notificaciones para mensajes web.
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

    usuario = (
        update.message
        .from_user
        .first_name
    )

    mensaje = update.message.text

    texto =f"{usuario}: {mensaje}"

    print("Telegram:", texto)

    socket.emit(
        "telegram_message",
        texto
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
            filters.TEXT & ~filters.COMMAND,
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
