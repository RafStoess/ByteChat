import os


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


def preguntar_ia(pregunta):

    return (
        "Modulo IA preparado. Proximamente respondere usando "
        "Gemini y algoritmos.txt."
    )
