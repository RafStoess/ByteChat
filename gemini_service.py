import base64
import os

import requests


GEMINI_MODELOS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash"
]

PROMPT_SISTEMA = """
Eres ByteBot IA, asistente academico de ByteChat Arena.
Tu fuente principal es el TeamBook PDF de programacion competitiva.
Tu funcion es explicar algoritmos, estructuras de datos y fragmentos del TeamBook.
No resuelvas problemas completos.
No generes soluciones finales de concursos.
No inventes algoritmos que no esten relacionados con el TeamBook.
No des codigo completo nuevo inventado.
Solo puedes mostrar codigo que provenga del TeamBook o estructuras generales del TeamBook.
Cuando el usuario pregunte por una categoria, lista los elementos disponibles.
Cuando pregunte por un algoritmo, explica brevemente para que sirve, cuando se usa,
su complejidad si aparece o se puede inferir, y muestra codigo del TeamBook si corresponde.
Si no encuentras algo en el TeamBook, dilo claramente.
Responde en espanol, breve y estructurado.
"""

MENSAJE_RECHAZO_PROBLEMA = (
    "No puedo resolver problemas completos del concurso. "
    "Puedo explicarte el algoritmo o mostrarte una estructura del TeamBook."
)


def cargar_contexto():

    ruta_contexto = os.path.join(
        os.path.dirname(__file__),
        "algoritmos.txt"
    )

    try:

        with open(ruta_contexto, "r", encoding="utf-8") as archivo:

            return archivo.read()

    except OSError:

        return ""


def cargar_teambook_pdf_base64():

    ruta_pdf = os.path.join(
        os.path.dirname(__file__),
        "static",
        "docs",
        "teambook.pdf"
    )

    try:

        with open(ruta_pdf, "rb") as archivo:

            return base64.b64encode(
                archivo.read()
            ).decode("ascii")

    except OSError:

        return ""


def parece_pedir_solucion_completa(pregunta):

    texto = pregunta.lower()

    frases_bloqueadas = [
        "resuelve este problema",
        "resuelve el problema",
        "soluciona este problema",
        "dame la solucion",
        "dame el codigo completo",
        "codigo completo",
        "solucion completa",
        "solve this problem",
        "full solution"
    ]

    return any(
        frase in texto
        for frase in frases_bloqueadas
    )


def extraer_texto_respuesta(data):

    candidatos = data.get("candidates", [])

    if not candidatos:

        return ""

    contenido = candidatos[0].get("content", {})
    partes = contenido.get("parts", [])

    textos = [
        parte.get("text", "")
        for parte in partes
        if parte.get("text")
    ]

    return "\n".join(textos).strip()


def construir_partes(pregunta, usar_pdf=True):

    partes = [
        {
            "text": (
                PROMPT_SISTEMA.strip() +
                "\n\nPregunta del usuario:\n" +
                pregunta.strip()
            )
        }
    ]

    pdf_base64 = cargar_teambook_pdf_base64() if usar_pdf else ""

    if pdf_base64:

        partes.append({
            "inline_data": {
                "mime_type": "application/pdf",
                "data": pdf_base64
            }
        })

    else:

        contexto = cargar_contexto()

        if contexto:

            partes.append({
                "text": (
                    "Contexto temporal de algoritmos.txt:\n" +
                    contexto
                )
            })

    return partes


def consultar_modelo(modelo, partes, api_key):

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/" +
        modelo +
        ":generateContent"
    )

    respuesta = requests.post(
        url,
        params={"key": api_key},
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": partes
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1200
            }
        },
        timeout=45
    )

    if not respuesta.ok:

        raise RuntimeError(
            f"Gemini respondio {respuesta.status_code}: {respuesta.text[:200]}"
        )

    texto = extraer_texto_respuesta(
        respuesta.json()
    )

    if not texto:

        raise RuntimeError("Gemini no devolvio texto.")

    return texto


def preguntar_ia(pregunta):

    pregunta = str(pregunta or "").strip()

    if not pregunta:

        return "Escribe una pregunta sobre algoritmos o estructuras del TeamBook."

    if parece_pedir_solucion_completa(pregunta):

        return MENSAJE_RECHAZO_PROBLEMA

    api_key = os.environ.get(
        "GEMINI_API_KEY",
        ""
    ).strip()

    if not api_key:

        return (
            "ByteBot IA no esta configurado todavia. "
            "Falta definir GEMINI_API_KEY en las variables de entorno."
        )

    partes = construir_partes(
        pregunta,
        usar_pdf=True
    )
    ultimo_error = ""

    for modelo in GEMINI_MODELOS:

        try:

            return consultar_modelo(
                modelo,
                partes,
                api_key
            )

        except Exception as error:

            ultimo_error = str(error)

    partes_fallback = construir_partes(
        pregunta,
        usar_pdf=False
    )

    for modelo in GEMINI_MODELOS:

        try:

            return consultar_modelo(
                modelo,
                partes_fallback,
                api_key
            )

        except Exception as error:

            ultimo_error = str(error)

    print(
        "Error Gemini:",
        ultimo_error
    )

    return (
        "ByteBot IA no pudo consultar Gemini en este momento. "
        "Intenta de nuevo o revisa la configuracion del servicio."
    )
