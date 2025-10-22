from datetime import datetime

def gerar_mensagem(loja, chamados):
    """
    Gera mensagem para um grupo de chamados da mesma loja,
    listando cada FSA e no final um bloco único de endereço.
    """
    blocos = []
    endereco_info = None

    for ch in chamados:
        linhas = [
            f"*{ch['key']}*",
            f"Loja: {loja}",
            f"PDV: {ch.get('pdv','--')}",
            f"*ATIVO: {ch.get('ativo','--')}*",
            f"Problema: {ch.get('problema','--')}",
            "***"
        ]
        blocos.append("\n".join(linhas))
        endereco_info = (
            ch.get('endereco','--'),
            ch.get('estado','--'),
            ch.get('cep','--'),
            ch.get('cidade','--')
        )

    if endereco_info:
        blocos.append(
            "\n".join([
                f"Endereço: {endereco_info[0]}",
                f"Estado: {endereco_info[1]}",
                f"CEP: {endereco_info[2]}",
                f"Cidade: {endereco_info[3]}"
            ])
        )
    
    # 🚨 Frase padrão no final
    blocos.append(
        "*SEMPRE AO CHEGAR NO LOCAL É NECESSÁRIO ACIONAR O SUPORTE E ENVIAR AS FOTOS NECESSÁRIAS*"
    )

    return "\n\n".join(blocos)

def verificar_duplicidade(chamados):
    """
    Retorna set de tuplas (pdv, ativo) que aparecem mais de uma vez.
    """
    seen = {}
    duplicates = set()
    for ch in chamados:
        key = (ch.get("pdv"), ch.get("ativo"))
        if key in seen:
            duplicates.add(key)
        else:
            seen[key] = True
    return duplicates
