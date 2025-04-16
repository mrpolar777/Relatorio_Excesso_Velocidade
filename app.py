import streamlit as st
import requests
import folium
import os
import tempfile
import time
import imgkit
from datetime import datetime
from pymongo import MongoClient
import xlsxwriter

# === CONFIGS INICIAIS ===
API_LOGIN = "http://teresinagps.rastrosystem.com.br/api_v2/login/"
API_VEICULOS = "http://teresinagps.rastrosystem.com.br/api_v2/veiculos/"
API_HISTORICO = "http://teresinagps.rastrosystem.com.br/api_v2/veiculo/historico/"

# === FUNÇÕES AUXILIARES ===
def autenticar(login, senha):
	payload = {"login": login, "senha": senha, "app": 4}
	r = requests.post(API_LOGIN, data=payload)
	r.raise_for_status()
	return r.json()["token"], r.json()["id"]

def listar_veiculos(token, usuario_id):
	headers = {"Authorization": f"token {token}"}
	r = requests.get(f"{API_VEICULOS}{usuario_id}/", headers=headers)
	r.raise_for_status()
	return r.json().get("dispositivos", [])

def consultar_historico(token, veiculo_id, data):
	headers = {"Authorization": f"token {token}", "Content-Type": "application/json"}
	payload = {
		"data": data.strftime("%d/%m/%Y"),
		"hora_ini": "00:00:00",
		"hora_fim": "23:59:59",
		"veiculo": veiculo_id
	}
	r = requests.post(API_HISTORICO, headers=headers, json=payload)
	r.raise_for_status()
	return r.json().get("veiculos", [])

def gerar_mapa_com_pontos(pontos):
	coord = [(p["latitude"], p["longitude"]) for p in pontos]
	mapa = folium.Map(location=coord[0], zoom_start=15, tiles="CartoDB positron")
	folium.PolyLine(coord, color="blue", weight=4.5).add_to(mapa)
	for p in pontos:
		vel = float(p.get("velocidade", 0))
		cor = "red" if vel > 50 else "green"
		folium.CircleMarker(location=(p["latitude"], p["longitude"]),
					  radius=5,
					  color=cor,
					  fill=True,
					  fill_opacity=0.7,
					  popup=f"Vel: {vel} km/h").add_to(mapa)
	return mapa

# === INTERFACE STREAMLIT ===
st.title("Relatório de Excesso de Velocidade")

MONGO_URI = st.secrets["MONGO_URI"] if "MONGO_URI" in st.secrets else st.text_input("MongoDB URI")
if MONGO_URI:
	client = MongoClient(MONGO_URI)
	db = client['relatorios_frota']
else:
	st.warning("Informe a URI do MongoDB para continuar.")
	st.stop()

with st.form("formulario"):
	st.subheader("Acesso à API")
	login = st.text_input("Login API")
	senha = st.text_input("Senha API", type="password")

	st.subheader("Parâmetros do Relatório")
	data_relatorio = st.date_input("Data", value=datetime.today())
	gerar = st.form_submit_button("Gerar Relatório")

if gerar:
	try:
		st.info("Autenticando e carregando veículos...")
		token, usuario_id = autenticar(login, senha)
		veiculos = listar_veiculos(token, usuario_id)

		registros = []
		imagens = {}
		temp_dir = tempfile.mkdtemp()

		progresso = st.progress(0)
		status = st.empty()
		total = len(veiculos)
		count = 0

		for v in veiculos:
			placa = v.get("placa")
			modelo = v.get("modelo")
			veiculo_id = v.get("veiculo_id")

			pontos = consultar_historico(token, veiculo_id, data_relatorio)
			if not pontos:
				continue

			velocidades = [float(p.get("velocidade", 0)) for p in pontos]
			if all(v <= 50 for v in velocidades):
				continue

			vel_max = max(velocidades)

			# Lógica de picos
			qtd_excesso = 0
			em_excesso = False
			for v in velocidades:
				if v > 50 and not em_excesso:
					qtd_excesso += 1
					em_excesso = True
				elif v <= 50:
					em_excesso = False

			# Salvar mapa HTML e PNG via imgkit
			mapa = gerar_mapa_com_pontos(pontos)
			mapa_html_nome = f"mapa_{placa}.html"
			mapa_html_path = os.path.join(temp_dir, mapa_html_nome)
			mapa.save(mapa_html_path)

			temp_img = os.path.join(temp_dir, f"mapa_{placa}.png")
			imgkit.from_file(mapa_html_path, temp_img)

			imagens[placa] = temp_img

			registros.append({
				"Data": data_relatorio.strftime("%d/%m/%Y"),
				"Veículo": modelo,
				"Placa": placa,
				"Velocidade Máxima": round(vel_max, 2),
				"Ocorrências > 50 km/h": qtd_excesso,
				"Ver Rota (Mapa)": f'=HYPERLINK("{mapa_html_path}", "Abrir Mapa")'
			})

			count += 1
			progresso.progress(count / total)
			status.text(f"Processando: {placa} ({count}/{total})")

		if not registros:
			st.warning("Nenhum veículo ultrapassou 50km/h no dia selecionado.")
			st.stop()

		planilha_path = os.path.join(temp_dir, f"relatorio_excesso_{data_relatorio.strftime('%d%m%Y')}.xlsx")
		workbook = xlsxwriter.Workbook(planilha_path)
		worksheet = workbook.add_worksheet("Relatório")

		headers = list(registros[0].keys()) + ["Imagem da Rota"]
		for col, h in enumerate(headers):
			worksheet.write(0, col, h)

		for row, reg in enumerate(registros, start=1):
			for col, key in enumerate(reg.keys()):
				worksheet.write(row, col, reg[key])
			img_path = imagens.get(reg["Placa"])
			if img_path:
				worksheet.set_row(row, 120)
				worksheet.insert_image(row, len(headers)-1, img_path, {'x_scale': 0.4, 'y_scale': 0.4})

		workbook.close()

		with open(planilha_path, "rb") as f:
			st.success("Relatório gerado com sucesso!")
			st.download_button("Baixar Relatório Excel", f, file_name=os.path.basename(planilha_path))

	except Exception as e:
		st.error(f"Erro ao gerar relatório: {e}")
