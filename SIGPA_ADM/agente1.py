import ollama
import json

CATALOGO = {
    "bidón 20L": 3500,
    "bidón 10L": 2000,
    "soda 1.5L": 800,
}

SYSTEM_PROMPT = f"""
Eres un asistente de pedidos para una distribuidora de agua purificada.
Sigue estos pasos EN ORDEN ESTRICTO, uno a la vez:

PASO 1 - SALUDO Y CATÁLOGO:
Saluda y muestra el catálogo. Pregunta qué producto desea.
Catálogo disponible: {', '.join(CATALOGO.keys())}

PASO 2 - CANTIDAD:
Cuando identifiques el producto solicitado, tu ÚNICA respuesta debe ser:
"¿Cuántas unidades de [nombre producto] deseas?"
No confirmes, no asumas, no menciones ningún número. Solo pregunta la cantidad.

PASO 3 - ¿AGREGAR MÁS? (SE REPITE SIEMPRE):
Después de confirmar la cantidad de CUALQUIER producto, SIEMPRE pregunta:
"¿Deseas agregar algún otro producto?"
- Si dice SÍ → vuelve al PASO 1 para el siguiente producto.
- Si dice NO → avanza al PASO 4.
NUNCA saltes este paso. Aunque ya hayas preguntado antes, vuelve a preguntar después de cada producto agregado.

PASO 4 - DIRECCIÓN:
Pregunta la dirección de despacho (calle, número, comuna).

PASO 5 - RESUMEN Y JSON:
Solo cuando tengas productos, cantidades Y dirección, responde ÚNICAMENTE
con este JSON sin texto adicional:
{{
  "pedido": [{{"producto": "...", "cantidad": N, "precio_unit": N}}],
  "direccion": "...",
  "total": N
}}

REGLAS:
- Haz UNA sola pregunta por mensaje.
- NUNCA asumas una cantidad. Siempre pregúntala.
- No inventes productos fuera del catálogo.
- No generes el JSON antes de tener todos los datos.
- Después de confirmar CADA producto y su cantidad, SIEMPRE pregunta si desea agregar más antes de continuar.
- Cuando el cliente mencione un producto, NUNCA incluyas un número de unidades en tu respuesta.
  Solo responde: "¿Cuántas unidades de [producto] deseas?" sin asumir nada.
- "bidón 20" significa el producto "bidón 20L", NO una cantidad. 
  El número en el nombre del producto NO es la cantidad pedida.
- tu funcion es solo responder como asistente de pedidos, no debes resolver ningun tipo de inquietud que no este definida en este prompt. Si el cliente hace una pregunta o comentario que no esté relacionado con el proceso de pedido, responde con:
"Lo siento, solo puedo ayudarte con pedidos de agua purificada. Por favor, dime qué producto deseas del catálogo."  
"""

def chat_agente(historial: list, mensaje_usuario: str) -> tuple[str, list]:
    historial.append({
        "role": "user",
        "content": mensaje_usuario
    })

    response = ollama.chat(
        model="llama3.2",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + historial
    )

    respuesta = response["message"]["content"]

    historial.append({
        "role": "assistant",
        "content": respuesta
    })

    return respuesta, historial


def intentar_extraer_pedido(texto: str) -> dict | None:
    try:
        inicio = texto.find("{")
        fin = texto.rfind("}") + 1
        if inicio != -1 and fin > inicio:
            return json.loads(texto[inicio:fin])
    except json.JSONDecodeError:
        pass
    return None


def main():
    print("=== Agente de Pedidos ADM ===")
    print("Escribe 'salir' para terminar\n")

    historial = []
    pedido_completado = None

    while pedido_completado is None:
        entrada = input("Tú: ").strip()

        if not entrada:
            continue

        if entrada.lower() == "salir":
            break

        respuesta, historial = chat_agente(historial, entrada)
        print(f"\nAgente: {respuesta}\n")

        pedido_completado = intentar_extraer_pedido(respuesta)

    if pedido_completado:
        print("\n✅ Pedido registrado:")
        print(json.dumps(pedido_completado, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()