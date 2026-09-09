from dotenv import load_dotenv 
import streamlit as st 
import requests 
import os 
import re 
import unicodedata 
from datetime import datetime 
from zoneinfo import ZoneInfo 
from sqlalchemy import create_engine, text 
import psycopg2

load_dotenv()

st.set_page_config(
    page_title="IA Gestão de Remanejamento",
    page_icon="🤖",
    layout="wide"
)

# ==========================================
# FUNÇÃO DE SEGURANÇA (Chaves e Senhas)
# ==========================================
def pegar_configuracao(chave, valor_padrao=None):
    try:
        if chave in st.secrets:
            return st.secrets[chave]
    except Exception:
        pass
    return os.getenv(chave, valor_padrao)

api_key = pegar_configuracao("GROQ_API_KEY")
MODEL_NAME = "llama-3.3-70b-versatile"

DATABASE_URL = os.getenv("DATABASE_URL") or pegar_configuracao("DATABASE_URL")

def init_database(db_uri):
    return create_engine(db_uri)

def testar_conexao(engine):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

if "engine" not in st.session_state:
    try:
        if not DATABASE_URL:
            raise ValueError("DATABASE_URL não configurada nas variáveis de ambiente ou secrets.")
        engine_padrao = init_database(DATABASE_URL)
        testar_conexao(engine_padrao)
        st.session_state.engine = engine_padrao
    except Exception as e:
        st.session_state.engine = None
        st.error(f"❌ Erro detalhado de conexão: {e}")
        st.info(f"DEBUG: DATABASE_URL={DATABASE_URL}")

def normalizar_texto(texto):
    if texto is None: return ""
    texto = str(texto).lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texto).strip()

def get_data_atual():
    fuso_horario = ZoneInfo("America/Sao_Paulo")
    agora = datetime.now(fuso_horario)
    dias_semana = {
        0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira", 
        3: "quinta-feira", 4: "sexta-feira", 5: "sábado", 6: "domingo"
    }
    return {
        "data": agora.strftime("%d/%m/%Y"),
        "dia_semana": dias_semana[agora.weekday()],
        "hora_atual": agora.time(),
        "datetime": agora 
    }

# ==========================================
# CONSULTAS AO BANCO DE DADOS
# ==========================================
def buscar_horarios(engine):
    sql = "SELECT id_horario, dia_semana, periodo_aula, hora_inicio, hora_fim FROM horarios ORDER BY id_horario"
    with engine.connect() as connection:
        return connection.execute(text(sql)).mappings().all()

def buscar_turmas(engine):
    sql = "SELECT id_turma, nome_turma FROM turma ORDER BY nome_turma"
    with engine.connect() as connection:
        return connection.execute(text(sql)).mappings().all()

def buscar_disciplinas(engine):
    sql = "SELECT id_disciplina, nome_disciplina FROM disciplina ORDER BY nome_disciplina"
    with engine.connect() as connection:
        return connection.execute(text(sql)).mappings().all()

def buscar_professores(engine):
    sql = "SELECT id_professor, nome_professor FROM professores ORDER BY nome_professor"
    with engine.connect() as connection:
        return connection.execute(text(sql)).mappings().all()

def buscar_professores_por_disciplina(engine, id_disciplina):
    sql = """
        SELECT DISTINCT p.id_professor, p.nome_professor
        FROM grade_aulas ga
        INNER JOIN professores p ON ga.id_professor = p.id_professor
        WHERE ga.id_disciplina = :id_disciplina
        ORDER BY p.nome_professor
    """
    with engine.connect() as connection:
        return connection.execute(text(sql), {"id_disciplina": id_disciplina}).mappings().all()

def buscar_professor_por_turma_disciplina_dia(engine, id_turma, id_disciplina, dia_semana):
    """Busca o professor específico para uma turma, disciplina e dia da semana na grade"""
    sql = """
        SELECT DISTINCT p.id_professor, p.nome_professor, h.dia_semana, h.periodo_aula
        FROM grade_aulas ga
        INNER JOIN professores p ON ga.id_professor = p.id_professor
        INNER JOIN horarios h ON ga.id_horario = h.id_horario
        WHERE ga.id_turma = :id_turma 
          AND ga.id_disciplina = :id_disciplina
    """
    with engine.connect() as connection:
        resultados = connection.execute(text(sql), {"id_turma": id_turma, "id_disciplina": id_disciplina}).mappings().all()
    
    # Filtra em Python para garantir flexibilidade com os nomes dos dias da semana
    for r in resultados:
        if normalizar_dia_semana(r["dia_semana"]) == normalizar_dia_semana(dia_semana):
            return r
    return None

def buscar_professores_disponiveis(engine, id_horario, id_professor_original=None):
    sql = """
        SELECT p.id_professor, p.nome_professor, COUNT(ga.id_grade) AS carga_horaria
        FROM professores p
        LEFT JOIN grade_aulas ga ON p.id_professor = ga.id_professor
        WHERE NOT EXISTS (
            SELECT 1 FROM grade_aulas ga_ocupado
            WHERE ga_ocupado.id_professor = p.id_professor AND ga_ocupado.id_horario = :id_horario
        )
    """
    params = {"id_horario": id_horario}
    
    if id_professor_original:
        sql += " AND p.id_professor != :id_professor_original"
        params["id_professor_original"] = id_professor_original

    sql += """
        GROUP BY p.id_professor, p.nome_professor
        ORDER BY carga_horaria ASC, p.nome_professor ASC
    """
    
    with engine.connect() as connection:
        return connection.execute(text(sql), params).mappings().all()

def professor_leciona_disciplina(engine, id_professor, id_disciplina):
    sql = "SELECT EXISTS (SELECT 1 FROM grade_aulas WHERE id_professor = :id_professor AND id_disciplina = :id_disciplina)"
    with engine.connect() as connection:
        return connection.execute(text(sql), {"id_professor": id_professor, "id_disciplina": id_disciplina}).scalar()

def buscar_disciplinas_professor(engine, id_professor):
    sql = """
        SELECT DISTINCT d.nome_disciplina
        FROM grade_aulas ga
        INNER JOIN disciplina d ON ga.id_disciplina = d.id_disciplina
        WHERE ga.id_professor = :id_professor
        ORDER BY d.nome_disciplina
    """
    with engine.connect() as connection:
        resultado = connection.execute(text(sql), {"id_professor": id_professor}).mappings().all()
    return [item["nome_disciplina"] for item in resultado]

def buscar_professor_original(engine, id_disciplina, id_turma):
    sql = """
        SELECT p.id_professor, p.nome_professor
        FROM grade_aulas ga
        INNER JOIN professores p ON ga.id_professor = p.id_professor
        WHERE ga.id_disciplina = :id_disciplina AND ga.id_turma = :id_turma
        LIMIT 1
    """
    with engine.connect() as connection:
        return connection.execute(text(sql), {"id_disciplina": id_disciplina, "id_turma": id_turma}).mappings().first()

# ==========================================
# REGRAS E IDENTIFICAÇÃO
# ==========================================
def normalizar_dia_semana(dia):
    dia_normalizado = normalizar_texto(dia)
    equivalencias = {
        "segunda-feira": ["segunda", "segunda feira", "2 feira", "2a feira", "2ª feira"],
        "terça-feira": ["terca", "terca feira", "terça", "3 feira", "3a feira", "3ª feira"],
        "quarta-feira": ["quarta", "quarta feira", "4 feira", "4a feira", "4ª feira"],
        "quinta-feira": ["quinta", "quinta feira", "5 feira", "5a feira", "5ª feira"],
        "sexta-feira": ["sexta", "sexta feira", "6 feira", "6a feira", "6ª feira"]
    }
    for dia_padrao, variacoes in equivalencias.items():
        if dia_normalizado == normalizar_texto(dia_padrao) or any(dia_normalizado == normalizar_texto(v) for v in variacoes):
            return dia_padrao
    return dia_normalizado

def identificar_dia_semana_na_pergunta(pergunta):
    pergunta_normalizada = normalizar_texto(pergunta)
    dias = ["segunda", "terça", "terca", "quarta", "quinta", "sexta"]
    for dia in dias:
        if re.search(rf"\b{dia}\b", pergunta_normalizada):
            return normalizar_dia_semana(dia)
    return None

def identificar_periodo(pergunta):
    pergunta_normalizada = normalizar_texto(pergunta)
    padrao_numero = re.search(r"(\d+)\s*(?:a|ª|o|º)?\s*aula", pergunta_normalizada)
    if padrao_numero:
        return int(padrao_numero.group(1))
    palavras = {
        "primeira aula": 1, "primeiro periodo": 1, "segunda aula": 2, "segundo periodo": 2,
        "terceira aula": 3, "terceiro periodo": 3, "quarta aula": 4, "quarto periodo": 4,
        "quinta aula": 5, "quinto periodo": 5, "sexta aula": 6, "sexto periodo": 6
    }
    for texto_busca, numero in palavras.items():
        if texto_busca in pergunta_normalizada:
            return numero
    return 1

def identificar_turma(pergunta, turmas):
    pergunta_normalizada = normalizar_texto(pergunta)
    for turma in turmas:
        if normalizar_texto(turma["nome_turma"]) in pergunta_normalizada:
            return turma
    padrao = re.search(r"(\d+)\s*(?:º|o|°)?\s*ano\s*([a-z])", pergunta_normalizada)
    if padrao:
        numero, letra = padrao.group(1), padrao.group(2)
        for turma in turmas:
            nome_norm = normalizar_texto(turma["nome_turma"])
            if numero in nome_norm and letra in nome_norm:
                return turma
    return None

def identificar_disciplina(pergunta, disciplinas):
    pergunta_normalizada = normalizar_texto(pergunta)
    disciplinas_ordenadas = sorted(disciplinas, key=lambda d: len(normalizar_texto(d["nome_disciplina"])), reverse=True)
    for disciplina in disciplinas_ordenadas:
        nome_banco = normalizar_texto(disciplina["nome_disciplina"])
        if re.search(rf"\b{re.escape(nome_banco)}s?\b", pergunta_normalizada):
            return disciplina
            
    grupos_sinonimos = [
        ["portugues", "lingua portuguesa", "port", "lp"],
        ["matematica", "mat", "mat."],
        ["educacao fisica", "ed fisica", "ed. fisica", "edf"],
        ["arte", "artes", "art"],
        ["historia", "hist"],
        ["geografia", "geo"],
        ["ciencias", "cien", "cie"],
        ["ingles", "ing", "lingua inglesa"],
        ["tutoria", "tutor"]
    ]
    for grupo in grupos_sinonimos:
        if any(re.search(rf"\b{re.escape(s)}s?\b", pergunta_normalizada) for s in grupo):
            for disciplina in disciplinas:
                if normalizar_texto(disciplina["nome_disciplina"]) in grupo or any(re.search(rf"\b{re.escape(s)}\b", normalizar_texto(disciplina["nome_disciplina"])) for s in grupo):
                    return disciplina
    return None

def horario_corresponde_dia(dia_banco, dia_procurado):
    return normalizar_dia_semana(dia_banco) == normalizar_dia_semana(dia_procurado)

def encontrar_horario_por_periodo(horarios, dia_semana, periodo):
    for horario in horarios:
        if horario_corresponde_dia(horario["dia_semana"], dia_semana):
            nums = re.findall(r"\d+", str(horario["periodo_aula"]))
            if nums and int(nums[0]) == periodo:
                return horario
    return None

def escolher_melhor_docente(engine, professores_disponiveis, disciplina):
    if not professores_disponiveis: return None
    candidatos_mesma_d, candidatos_outras_d = [], []
    
    for professor in professores_disponiveis:
        leciona = professor_leciona_disciplina(engine, professor["id_professor"], disciplina["id_disciplina"])
        prof_dict = {
            "id_professor": professor["id_professor"], 
            "nome_professor": professor["nome_professor"],
            "carga_horaria": professor["carga_horaria"], 
            "mesma_disciplina": leciona 
        }
        if leciona: candidatos_mesma_d.append(prof_dict)
        else: candidatos_outras_d.append(prof_dict)
            
    if candidatos_mesma_d:
        return sorted(candidatos_mesma_d, key=lambda p: (p["carga_horaria"], p["nome_professor"]))[0]
    if candidatos_outras_d:
        return sorted(candidatos_outras_d, key=lambda p: (p["carga_horaria"], p["nome_professor"]))[0]
    return None

def processar_ausencia(pergunta, engine):
    data_info = get_data_atual()
    dia_semana = identificar_dia_semana_na_pergunta(pergunta) or data_info["dia_semana"]
    
    turmas = buscar_turmas(engine)
    disciplinas = buscar_disciplinas(engine)
    horarios = buscar_horarios(engine)
    
    disciplina = identificar_disciplina(pergunta, disciplinas)
    if not disciplina: return "⚠️ Não consegui identificar a **disciplina** na pergunta."
        
    turma = identificar_turma(pergunta, turmas)
    if not turma: return "⚠️ Não consegui identificar a **turma** na pergunta."
        
    periodo = identificar_periodo(pergunta)
    horario = encontrar_horario_por_periodo(horarios, dia_semana, periodo)
    if not horario: return f"⚠️ Não encontrei horário para **{dia_semana}** no período **{periodo}**."
        
    professor_original = buscar_professor_original(engine, disciplina["id_disciplina"], turma["id_turma"])
    id_orig_val = professor_original["id_professor"] if professor_original else None

    professores_disponiveis = buscar_professores_disponiveis(engine, horario["id_horario"], id_orig_val)
    
    if not professores_disponiveis: return "⚠️ Nenhum docente disponível para substituição neste horário."
        
    melhor_docente = escolher_melhor_docente(engine, professores_disponiveis, disciplina)
    if not melhor_docente: return "⚠️ Não foi possível selecionar um docente para substituição."
        
    disciplinas_docente = buscar_disciplinas_professor(engine, melhor_docente["id_professor"])
    
    relatorio = f"**Docente sugerido para substituição:** {melhor_docente['nome_professor']}\n\n"
    if professor_original:
        relatorio += f"**Docente ausente identificado:** {professor_original['nome_professor']}\n\n"
    else:
        relatorio += f"*(Não foi possível vincular o titular exato desta aula no banco de dados)*\n\n"
        
    if melhor_docente["mesma_disciplina"]:
        relatorio += f"**Motivo:** O(a) docente **{melhor_docente['nome_professor']}** está disponível na {horario['periodo_aula']} de {dia_semana} e já leciona **{disciplina['nome_disciplina']}**, tendo prioridade por menor carga horária.\n\n"
    else:
        relatorio += f"**Motivo:** O(a) docente **{melhor_docente['nome_professor']}** está disponível e foi selecionado por possuir menor carga horária.\n\n"
        
    relatorio += f"**Disciplinas cadastradas:** {', '.join(disciplinas_docente) if disciplinas_docente else 'Nenhuma.'}\n\n"
    relatorio += f"---\n📅 **Dia:** {dia_semana.title()} | 🏫 **Turma:** {turma['nome_turma']} | 📚 **Disciplina:** {disciplina['nome_disciplina']} | 🕐 **Período:** {horario['periodo_aula']} ({horario['hora_inicio'].strftime('%H:%M')} às {horario['hora_fim'].strftime('%H:%M')})"
    
    return relatorio

# ==========================================
# LGPD: DATA MASKING
# ==========================================
def aplicar_data_masking(texto, engine):
    professores = buscar_professores(engine)
    mapa_nomes = {}
    texto_mascarado = texto
    
    for prof in professores:
        nome_real = prof["nome_professor"]
        id_anonimo = f"[DOCENTE_ID_{prof['id_professor']}]"
        
        if re.search(rf"\b{re.escape(nome_real)}\b", texto_mascarado, flags=re.IGNORECASE):
            mapa_nomes[id_anonimo] = nome_real
            texto_mascarado = re.sub(rf"\b{re.escape(nome_real)}\b", id_anonimo, texto_mascarado, flags=re.IGNORECASE)
            
    return texto_mascarado, mapa_nomes

def remover_data_masking(texto_mascarado, mapa_nomes):
    texto_desmascarado = texto_mascarado
    for id_anonimo, nome_real in mapa_nomes.items():
        texto_desmascarado = texto_desmascarado.replace(id_anonimo, nome_real)
    return texto_desmascarado

# ==========================================
# INTEGRAÇÃO IA COM PROTEÇÃO DE DADOS
# ==========================================
def chamar_ia_generativa(pergunta_usuario_mascarada, contexto_banco_mascarado):
    if not api_key: return contexto_banco_mascarado
    prompt = f"Você é um assistente escolar de remanejamento. Pergunta: '{pergunta_usuario_mascarada}'. Dados do banco: {contexto_banco_mascarado}. Escreva uma resposta clara, empática e profissional formatada em Markdown, baseando-se apenas nos dados informados."
    
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}
    try:
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data, timeout=10)
        if response.status_code == 200: 
            return response.json()['choices'][0]['message']['content']
        return contexto_banco_mascarado
    except Exception: 
        return contexto_banco_mascarado

def get_response(pergunta, engine):
    texto_norm = normalizar_texto(pergunta)
    
    # Intenção 1: Ausência / Substituição / Remanejamento
    if any(k in texto_norm for k in ["falta", "faltou", "ausente", "substituir", "remanejamento", "substituto"]):
        resultado_banco = processar_ausencia(pergunta, engine)
        
        pergunta_mascarada, mapa_pergunta = aplicar_data_masking(pergunta, engine)
        resultado_mascarado, mapa_resultado = aplicar_data_masking(resultado_banco, engine)
        
        mapa_completo = {**mapa_pergunta, **mapa_resultado}
        
        resposta_ia_mascarada = chamar_ia_generativa(pergunta_mascarada, resultado_mascarado)
        return remover_data_masking(resposta_ia_mascarada, mapa_completo)

    # Intenção 2: Pergunta específica sobre qual professor está em uma turma/disciplina em um dia específico (ex: "qual professor esta em tutoria no 9 ano A sexta feira")
    elif "turma" in texto_norm or "ano" in texto_norm or any(d in texto_norm for d in ["segunda", "terca", "quarta", "quinta", "sexta"]):
        turmas = buscar_turmas(engine)
        disciplinas = buscar_disciplinas(engine)
        
        turma = identificar_turma(pergunta, turmas)
        disciplina = identificar_disciplina(pergunta, disciplinas)
        dia_semana = identificar_dia_semana_na_pergunta(pergunta) or get_data_atual()["dia_semana"]
        
        if turma and disciplina:
            prof_grade = buscar_professor_por_turma_disciplina_dia(engine, turma["id_turma"], disciplina["id_disciplina"], dia_semana)
            if prof_grade:
                return f"👤 O(a) professor(a) responsável por **{disciplina['nome_disciplina']}** no(a) **{turma['nome_turma']}** na **{dia_semana.title()}** é **{prof_grade['nome_professor']}**."
            else:
                return f"⚠️ Não encontrei nenhum registro de **{disciplina['nome_disciplina']}** para o(a) **{turma['nome_turma']}** na **{dia_semana.title()}**."

    # Intenção 3: Listar todos os professores de uma disciplina geral
    elif "professor" in texto_norm or "professores" in texto_norm:
        disciplinas = buscar_disciplinas(engine)
        disciplina = identificar_disciplina(pergunta, disciplinas)
        
        if not disciplina:
            return "⚠️ Não consegui identificar a **disciplina** na sua pergunta para listar os professores."
            
        professores = buscar_professores_por_disciplina(engine, disciplina["id_disciplina"])
        
        if not professores:
            return f"📚 Não encontrei professores cadastrados lecionando **{disciplina['nome_disciplina']}** na grade atual."
            
        nomes = [p["nome_professor"] for p in professores]
        return f"📚 **Professores de {disciplina['nome_disciplina']}:**\n\n" + "\n".join([f"- {nome}" for nome in nomes])

    return "Olá! Posso ajudar a verificar ausências, sugerir substitutos, informar quem leciona uma disciplina ou consultar a grade de horários."

# ==========================================
# GERENCIAMENTO DE CONVERSAS NO HISTÓRICO
# ==========================================
if "historico_conversas" not in st.session_state:
    st.session_state.historico_conversas = {
        "Nova Conversa": [
            {"role": "assistant", "content": "Olá! 👋\n\nSou o Sistema de Apoio à Decisão para remanejamento docente. Como posso ajudar hoje?"}
        ]
    }

if "conversa_atual" not in st.session_state:
    st.session_state.conversa_atual = "Nova Conversa"

# ==========================================
# INTERFACE STREAMLIT
# ==========================================
with st.sidebar:
    st.title("🏫 Gestão de Remanejamento")
    st.markdown("---")
    
    if st.button("➕ Nova Conversa", use_container_width=True):
        num_conversas = len(st.session_state.historico_conversas) + 1
        nome_nova = f"Conversa {num_conversas}"
        st.session_state.historico_conversas[nome_nova] = [
            {"role": "assistant", "content": "Olá! 👋 Como posso ajudar no remanejamento ou consultas de hoje?"}
        ]
        st.session_state.conversa_atual = nome_nova
        st.rerun()
        
    st.markdown("---")
    st.write("📂 **Histórico de Conversas**")
    
    for nome_conv in list(st.session_state.historico_conversas.keys()):
        if st.button(nome_conv, use_container_width=True, key=f"btn_{nome_conv}"):
            st.session_state.conversa_atual = nome_conv
            st.rerun()
            
    st.markdown("---")
    data_info = get_data_atual()
    st.caption(f"📅 {data_info['dia_semana'].title()}, {data_info['data']}")

chat_ativo = st.session_state.historico_conversas[st.session_state.conversa_atual]

st.title("🤖 Sistema de Apoio à Decisão")
st.markdown(f"*Chat ativo: {st.session_state.conversa_atual}*")
st.markdown("---")

for m in chat_ativo:
    with st.chat_message(m["role"]): st.markdown(m["content"])

user_query = st.chat_input("Ex: Qual professor está em tutoria no 9º Ano A sexta-feira?")
if user_query:
    chat_ativo.append({"role": "user", "content": user_query})
    with st.chat_message("user"): st.markdown(user_query)
    
    if not st.session_state.get("engine"):
        ans = "⚠️ O sistema não está conectado ao banco de dados."
        with st.chat_message("assistant"): st.markdown(ans)
        chat_ativo.append({"role": "assistant", "content": ans})
    else:
        with st.chat_message("assistant"):
            with st.spinner("🔍 Consultando grade de horários..."):
                try:
                    ans = get_response(user_query, st.session_state.engine)
                    st.markdown(ans)
                    chat_ativo.append({"role": "assistant", "content": ans})
                except Exception as e: 
                    st.error(f"Erro ao processar: {e}")