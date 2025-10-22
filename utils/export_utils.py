import pandas as pd
from fpdf import FPDF

def chamados_to_csv(chamados, filename="chamados_exportados.csv"):
    df = pd.DataFrame(chamados)
    df.to_csv(filename, index=False)
    return filename

def chamados_to_pdf(chamados, filename="chamados_exportados.pdf"):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    for ch in chamados:
        pdf.multi_cell(0, 10,
            f"Chamado: {ch['key']}\n"
            f"Loja: {ch.get('loja','--')}\n"
            f"PDV: {ch.get('pdv','--')}\n"
            f"Ativo: {ch.get('ativo','--')}\n"
            f"Problema: {ch.get('problema','--')}\n"
            f"Data Agendada: {ch.get('data_agendada','--')}\n"
            f"Endereço: {ch.get('endereco','--')}\n"
            f"Cidade: {ch.get('cidade','--')} - {ch.get('estado','--')} (CEP: {ch.get('cep','--')})\n"
            "--------------------------------------------"
        )
    pdf.output(filename)
    return filename
