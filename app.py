import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename

DATABASE = os.environ.get("DATABASE_PATH", "metrifiy.db")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.secret_key = os.environ.get("SECRET_KEY", "metrifypremium-secret")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            sku TEXT UNIQUE,
            custo_unitario REAL NOT NULL DEFAULT 0,
            preco_venda_sugerido REAL NOT NULL DEFAULT 0,
            estoque_inicial INTEGER NOT NULL DEFAULT 0,
            estoque_atual INTEGER NOT NULL DEFAULT 0,
            curva TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            produto_id INTEGER NOT NULL,
            data_venda TEXT,
            quantidade INTEGER NOT NULL,
            preco_venda_unitario REAL NOT NULL,
            receita_total REAL NOT NULL,
            custo_total REAL NOT NULL,
            margem_contribuicao REAL NOT NULL,
            origem TEXT,
            numero_venda_ml TEXT,
            lote_importacao TEXT,
            FOREIGN KEY (produto_id) REFERENCES produtos (id)
        )
    """)

    conn.commit()
    conn.close()

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}

def parse_data_venda(texto):
    if isinstance(texto, datetime):
        return texto
    if not isinstance(texto, str) or not texto.strip():
        return None
    try:
        partes = texto.split()
        dia = int(partes[0])
        mes_nome = partes[2].lower()
        ano = int(partes[4])
        hora_min = partes[5]
        hora, minuto = hora_min.split(":")
        return datetime(ano, MESES_PT[mes_nome], int(dia), int(hora), int(minuto))
    except Exception:
        return None

def importar_vendas_ml(caminho_arquivo, conn):
    import pandas as pd

    lote_id = datetime.now().isoformat(timespec="seconds")

    df = pd.read_excel(
        caminho_arquivo,
        sheet_name="Vendas BR",
        header=5
    )
    if "N.º de venda" not in df.columns:
        raise ValueError("Planilha não está no formato esperado: coluna 'N.º de venda' não encontrada.")

    df = df[df["N.º de venda"].notna()]

    cur = conn.cursor()

    vendas_importadas = 0
    vendas_sem_sku = 0
    vendas_sem_produto = 0

    for _, row in df.iterrows():
        sku = str(row.get("SKU") or "").strip()
        if not sku:
            vendas_sem_sku += 1
            continue

        cur.execute("SELECT id, nome, custo_unitario FROM produtos WHERE sku = ?", (sku,))
        produto = cur.fetchone()
        if not produto:
            vendas_sem_produto += 1
            continue

        produto_id = produto["id"]
        custo_unitario = float(produto["custo_unitario"] or 0.0)

        data_venda_raw = row.get("Data da venda")
        data_venda = parse_data_venda(data_venda_raw)
        unidades = row.get("Unidades")
        try:
            unidades = int(unidades) if unidades == unidades else 0
        except Exception:
            unidades = 0

        total_brl = row.get("Total (BRL)")
        try:
            receita_total = float(total_brl) if total_brl == total_brl else 0.0
        except Exception:
            receita_total = 0.0

        preco_medio_venda = receita_total / unidades if unidades > 0 else 0.0
        custo_total = custo_unitario * unidades
        margem_contribuicao = receita_total - custo_total
        numero_venda_ml = str(row.get("N.º de venda"))

        cur.execute("""
            INSERT INTO vendas (
                produto_id, data_venda, quantidade, preco_venda_unitario,
                receita_total, custo_total, margem_contribuicao,
                origem, numero_venda_ml, lote_importacao
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            produto_id,
            data_venda.isoformat() if data_venda else None,
            unidades,
            preco_medio_venda,
            receita_total,
            custo_total,
            margem_contribuicao,
            "Mercado Livre",
            numero_venda_ml,
            lote_id,
        ))

        # atualiza estoque
        cur.execute("UPDATE produtos SET estoque_atual = estoque_atual - ? WHERE id = ?", (unidades, produto_id))

        vendas_importadas += 1

    conn.commit()

    return {
        "lote_id": lote_id,
        "vendas_importadas": vendas_importadas,
        "vendas_sem_sku": vendas_sem_sku,
        "vendas_sem_produto": vendas_sem_produto,
    }

@app.route("/")
def dashboard():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM produtos")
    total_produtos = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(estoque_atual), 0) FROM produtos")
    estoque_total = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(receita_total), 0) FROM vendas")
    receita_total = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(margem_contribuicao), 0) FROM vendas")
    lucro_total = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(
            AVG(
                CASE WHEN receita_total > 0
                     THEN (margem_contribuicao / receita_total) * 100
                     ELSE NULL END
            ), 0)
    """)
    margem_media = cur.fetchone()[0]

    cur.execute("""SELECT COALESCE(AVG(preco_venda_unitario), 0) FROM vendas""")
    ticket_medio = cur.fetchone()[0]

    cur.execute("""
        SELECT p.nome, SUM(v.quantidade) as qtd
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        GROUP BY p.id
        ORDER BY qtd DESC
        LIMIT 1
    """)
    produto_mais_vendido = cur.fetchone()

    cur.execute("""
        SELECT p.nome, SUM(v.margem_contribuicao) as lucro
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        GROUP BY p.id
        ORDER BY lucro DESC
        LIMIT 1
    """)
    produto_maior_lucro = cur.fetchone()

    cur.execute("""
        SELECT p.nome, SUM(v.margem_contribuicao) as margem
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        GROUP BY p.id
        ORDER BY margem ASC
        LIMIT 1
    """)
    produto_pior_margem = cur.fetchone()

    conn.close()

    return render_template(
        "dashboard.html",
        total_produtos=total_produtos,
        estoque_total=estoque_total,
        receita_total=receita_total,
        lucro_total=lucro_total,
        margem_media=margem_media,
        ticket_medio=ticket_medio,
        comissao_total=0,
        produto_mais_vendido=produto_mais_vendido,
        produto_maior_lucro=produto_maior_lucro,
        produto_pior_margem=produto_pior_margem,
    )

@app.route("/produtos")
def lista_produtos():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM produtos ORDER BY nome")
    produtos = cur.fetchall()
    conn.close()
    return render_template("produtos.html", produtos=produtos)

@app.route("/produtos/novo", methods=["GET", "POST"])
def novo_produto():
    if request.method == "POST":
        nome = request.form["nome"]
        sku = request.form["sku"]
        custo_unitario = float(request.form.get("custo_unitario", 0) or 0)
        preco_venda_sugerido = float(request.form.get("preco_venda_sugerido", 0) or 0)
        estoque_inicial = int(request.form.get("estoque_inicial", 0) or 0)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO produtos (nome, sku, custo_unitario, preco_venda_sugerido,
                                  estoque_inicial, estoque_atual)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (nome, sku, custo_unitario, preco_venda_sugerido,
                estoque_inicial, estoque_inicial))
        conn.commit()
        conn.close()
        flash("Produto cadastrado com sucesso!", "success")
        return redirect(url_for("lista_produtos"))

    return render_template("produto_form.html", produto=None)

@app.route("/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
def editar_produto(produto_id):
    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST":
        nome = request.form["nome"]
        sku = request.form["sku"]
        custo_unitario = float(request.form.get("custo_unitario", 0) or 0)
        preco_venda_sugerido = float(request.form.get("preco_venda_sugerido", 0) or 0)
        estoque_atual = int(request.form.get("estoque_atual", 0) or 0)

        cur.execute("""
            UPDATE produtos
            SET nome = ?, sku = ?, custo_unitario = ?, preco_venda_sugerido = ?,
                estoque_atual = ?
            WHERE id = ?
        """, (nome, sku, custo_unitario, preco_venda_sugerido,
                estoque_atual, produto_id))
        conn.commit()
        conn.close()
        flash("Produto atualizado!", "success")
        return redirect(url_for("lista_produtos"))

    cur.execute("SELECT * FROM produtos WHERE id = ?", (produto_id,))
    produto = cur.fetchone()
    conn.close()
    if not produto:
        flash("Produto não encontrado.", "danger")
        return redirect(url_for("lista_produtos"))
    return render_template("produto_form.html", produto=produto)

@app.route("/produtos/<int:produto_id>/excluir", methods=["POST"])
def excluir_produto(produto_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
    conn.commit()
    conn.close()
    flash("Produto excluído.", "success")
    return redirect(url_for("lista_produtos"))

@app.route("/vendas")
def lista_vendas():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT v.id, v.data_venda, v.quantidade, v.preco_venda_unitario,
               v.receita_total, v.margem_contribuicao,
               v.origem, v.numero_venda_ml, v.lote_importacao,
               p.nome
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        ORDER BY v.data_venda DESC NULLS LAST, v.id DESC
    """)
    vendas = cur.fetchall()

    cur.execute("""
        SELECT lote_importacao,
               COUNT(*) as qtd_vendas,
               COALESCE(SUM(receita_total), 0) as receita_lote
        FROM vendas
        WHERE lote_importacao IS NOT NULL
        GROUP BY lote_importacao
        ORDER BY lote_importacao DESC
    """)
    lotes = cur.fetchall()

    conn.close()
    return render_template("vendas.html", vendas=vendas, lotes=lotes)

@app.route("/vendas/<int:venda_id>/editar", methods=["GET", "POST"])
def editar_venda(venda_id):
    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST":
        quantidade = int(request.form["quantidade"])
        preco_venda_unitario = float(request.form["preco_venda_unitario"])
        custo_total = float(request.form["custo_total"])

        receita_total = quantidade * preco_venda_unitario
        margem_contribuicao = receita_total - custo_total

        cur.execute("""
            UPDATE vendas
            SET quantidade = ?, preco_venda_unitario = ?, receita_total = ?,
                margem_contribuicao = ?
            WHERE id = ?
        """, (quantidade, preco_venda_unitario, receita_total,
                margem_contribuicao, venda_id))
        conn.commit()
        conn.close()
        flash("Venda atualizada com sucesso!", "success")
        return redirect(url_for("lista_vendas"))

    cur.execute("""
        SELECT v.id, v.data_venda, v.quantidade, v.preco_venda_unitario,
               v.custo_total, p.nome
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        WHERE v.id = ?
    """, (venda_id,))
    venda = cur.fetchone()
    conn.close()

    if not venda:
        flash("Venda não encontrada.", "danger")
        return redirect(url_for("lista_vendas"))

    return render_template("editar_venda.html", venda=venda)

@app.route("/vendas/<int:venda_id>/excluir", methods=["POST"])
def excluir_venda(venda_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM vendas WHERE id = ?", (venda_id,))
    conn.commit()
    conn.close()
    flash("Venda excluída com sucesso!", "success")
    return redirect(url_for("lista_vendas"))

@app.route("/vendas/lote/<lote_id>/excluir", methods=["POST"])
def excluir_lote_vendas(lote_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM vendas WHERE lote_importacao = ?", (lote_id,))
    conn.commit()
    conn.close()
    flash("Lote de importação excluído com sucesso!", "success")
    return redirect(url_for("lista_vendas"))

@app.route("/importar_ml", methods=["GET", "POST"])
def importar_ml_view():
    if request.method == "POST":
        if "arquivo" not in request.files:
            flash("Nenhum arquivo enviado.", "danger")
            return redirect(request.url)
        file = request.files["arquivo"]
        if file.filename == "":
            flash("Selecione um arquivo.", "danger")
            return redirect(request.url)
        filename = secure_filename(file.filename)
        caminho = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(caminho)

        conn = get_db()
        try:
            resumo = importar_vendas_ml(caminho, conn)
            flash(
                f"Importação concluída. Lote {resumo['lote_id']} - "
                f"{resumo['vendas_importadas']} vendas importadas, "
                f"{resumo['vendas_sem_sku']} sem SKU, "
                f"{resumo['vendas_sem_produto']} sem produto cadastrado.",
                "success",
            )
        except Exception as e:
            flash(f"Erro na importação: {e}", "danger")
        finally:
            conn.close()
        return redirect(url_for("importar_ml_view"))

    return render_template("importar_ml.html")

@app.route("/relatorio_lucro")
def relatorio_lucro():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.nome,
               SUM(v.quantidade) as qtd,
               SUM(v.receita_total) as receita,
               SUM(v.custo_total) as custo,
               SUM(v.margem_contribuicao) as margem
        FROM vendas v
        JOIN produtos p ON p.id = v.produto_id
        GROUP BY p.id
        ORDER BY margem DESC
    """)

    linhas = cur.fetchall()
    conn.close()
    return render_template("relatorio_lucro.html", linhas=linhas)

@app.route("/estoque")
def estoque_view():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT nome, sku, estoque_atual, custo_unitario FROM produtos ORDER BY nome")
    produtos = cur.fetchall()
    conn.close()
    return render_template("estoque.html", produtos=produtos)

@app.route("/configuracoes")
def configuracoes_view():
    return render_template("configuracoes.html")

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
