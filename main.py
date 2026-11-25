"""
main.py
-------
Ponto de entrada da aplicação.
Constrói a interface gráfica com customtkinter e orquestra os módulos
cisco_serial.py e templates.py.
"""

import threading
import time
import os
import subprocess
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
from typing import Optional, List, Dict

import serial
import customtkinter as ctk

from cisco_serial import CiscoSerial, listar_portas, SwitchAcessoNegadoException
from cisco_ssh import (
    CiscoSSHClient,
    parse_interfaces_status,
    parse_cdp_neighbors,
    parse_mac_address_table,
    parse_cme_sip_status,
    normalize_interface_name,
)
from templates import (
    ConfiguracaoSwitch,
    VlanConfig,
    MODELOS_DISPONIVEIS,
    gerar_comandos,
    CiscoTemplates,
    group_interfaces,
)
import inventory

# ---------------------------------------------------------------------------
# Paleta de cores e constantes visuais (Dark Corporativo GitHub/CME Style)
# ---------------------------------------------------------------------------
COR_BG_ESCURO       = "#0d1117"   # fundo principal
COR_FRAME           = "#161b22"   # painéis internos
COR_FRAME_BORDA     = "#30363d"   # bordas dos frames
COR_DESTAQUE        = "#58a6ff"   # azul destaque corporativo
COR_VERDE           = "#2ea043"   # status conectado / sucesso
COR_VERMELHO        = "#da3633"   # status desconectado / erro
COR_AMARELO         = "#d29922"   # aviso / atenção
COR_CINZA           = "#8b949e"   # texto secundário
COR_TERMINAL_BG     = "#0d1117"   # fundo do terminal
COR_BOTAO_PRIMARIO  = "#1f6feb"   # botão primário
COR_BOTAO_HOVER     = "#388bfd"   # hover botão primário
FONTE_TERMINAL      = ("Consolas", 11)
FONTE_LABEL         = ("Segoe UI", 11)
FONTE_LABEL_BOLD    = ("Segoe UI", 11, "bold")
FONTE_TITULO        = ("Segoe UI", 13, "bold")


# ---------------------------------------------------------------------------
# Classe principal da aplicação
# ---------------------------------------------------------------------------

class AppSwitchManager(ctk.CTk):
    """
    Janela principal do Gerenciador de Switches Cisco.
    Organizada em três colunas:
      - Esquerda  : Conexão Serial + Seleção de Hardware
      - Centro    : Parâmetros de Configuração Day-0
      - Direita   : Terminal de Logs em tempo real
    """

    # ------------------------------------------------------------------
    # Inicialização
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()

        # Configuração global do customtkinter
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("PROVISIONADOR DE SWITCH")
        self.geometry("1300x820")
        self.minsize(1100, 700)
        self.configure(fg_color=COR_BG_ESCURO)

        # Estado interno - Serial (Day-0)
        self._serial_client: Optional[CiscoSerial] = None
        self.serial_conn: Optional[serial.Serial] = None
        self._thread_envio: Optional[threading.Thread] = None
        self._log_linhas: List[str] = []          # cache de linhas do log
        self._vlans_adicionais: List[VlanConfig] = []
        self._historico_comandos: List[str] = []  # histórico de comandos manuais do terminal
        self._idx_historico: int = -1
        self._entry_comando: Optional[ctk.CTkEntry] = None
        self.textbox_terminal: Optional[tk.Text] = None
        self.entry_serial_user: Optional[ctk.CTkEntry] = None
        self.entry_serial_pass: Optional[ctk.CTkEntry] = None
        self.btn_auto_login: Optional[ctk.CTkButton] = None
        self.btn_logout_console: Optional[ctk.CTkButton] = None
        self.esperando_resposta: bool = False
        self._provisionamento_ativo: bool = False
        self._buffer_provisionamento: str = ""

        # Estado interno - Dashboard SSH e COM
        self.ssh_client: Optional[CiscoSSHClient] = None
        self._thread_ssh: Optional[threading.Thread] = None
        self._var_ssh_ip   = ctk.StringVar()
        self._var_ssh_user = ctk.StringVar(value="admin")
        self._var_ssh_pass = ctk.StringVar()
        self._var_modo_conexao = ctk.StringVar(value="SSH (Rede IP)")
        self._var_dash_com_porta = ctk.StringVar()
        self._var_dash_com_baud = ctk.StringVar(value="9600")
        self._captura_serial_ativa: bool = False
        self._buffer_captura_serial: str = ""
        self._conexao_ativa_dashboard: Optional[str] = None  # "SSH" ou "COM"
        self._linhas_portas: List[dict] = []
        self.textbox_preview_ssh: Optional[ctk.CTkTextbox] = None

        # Estado interno - Funcionalidades V1.5 (Bulk, CDP e Rollback)
        self._ultimo_snapshot_rollback: dict = {}
        self._timestamp_rollback: str = ""
        self._cdp_vizinhos: dict = {}
        self._var_bulk_modo = ctk.StringVar(value="Access")
        self._var_bulk_vlan = ctk.StringVar(value="10")
        self._var_bulk_desc = ctk.StringVar(value="")
        self._var_bulk_estado = ctk.StringVar(value="Ativa (no shutdown)")
        self._var_bulk_poe = ctk.StringVar(value="Auto (Ligado)")
        self._var_sel_todos = ctk.BooleanVar(value=False)
        self.entry_ssh_password: Optional[ctk.CTkEntry] = None
        self.btn_comando_manual: Optional[ctk.CTkButton] = None

        # Funcionalidades V2.0 NMS (MAC Tracking, PoE e Ping Vault)
        self._mac_por_porta: dict = {}
        self.btn_ssh_macs: Optional[ctk.CTkButton] = None
        self.btn_backup_massivo: Optional[ctk.CTkButton] = None
        self._vault_status_labels: dict = {}
        self._ping_gen_id: int = 0

        # Botões de Navegação Superior (Abas Flat)
        self.btn_tab_vault: Optional[ctk.CTkButton] = None
        self.btn_tab_serial: Optional[ctk.CTkButton] = None
        self.btn_tab_ssh: Optional[ctk.CTkButton] = None
        self.btn_abrir_cme: Optional[ctk.CTkButton] = None

        # Estado interno - Hosts Vault (V2.0)
        self._var_vault_busca = ctk.StringVar()
        self._var_vault_grupo_filtro = ctk.StringVar(value="Todos os Grupos")
        self._frame_scroll_vault: Optional[ctk.CTkScrollableFrame] = None
        self._lbl_total_vault: Optional[ctk.CTkLabel] = None
        self._combo_vault_grupos: Optional[ctk.CTkOptionMenu] = None

        # Variáveis de controle dos widgets tkinter
        self._var_porta      = ctk.StringVar()
        self._var_baudrate   = ctk.StringVar(value="9600")
        self._var_modelo     = ctk.StringVar(value=MODELOS_DISPONIVEIS[0])
        self._var_hostname   = ctk.StringVar()
        self._var_secret     = ctk.StringVar()
        self._var_vlan_mgmt  = ctk.StringVar(value="1")
        self._var_ip_mgmt    = ctk.StringVar()
        self._var_mascara     = ctk.StringVar(value="255.255.255.0")
        self._var_gateway    = ctk.StringVar()
        self._var_vlan_id    = ctk.StringVar()
        self._var_vlan_nome  = ctk.StringVar()

        self._construir_layout()
        self._atualizar_portas()   # popula o combobox ao iniciar

    # ------------------------------------------------------------------
    # Construção do Layout
    # ------------------------------------------------------------------

    def _construir_layout(self) -> None:
        """Monta a interface dividida em abas (Serial Day-0 e Dashboard SSH Day-1)."""

        # Título da aplicação e Menu Superior de Abas Flat
        frame_titulo = ctk.CTkFrame(self, fg_color=COR_FRAME, corner_radius=0, height=58)
        frame_titulo.pack(fill="x", padx=0, pady=0)
        frame_titulo.pack_propagate(False)

        ctk.CTkLabel(
            frame_titulo,
            text="⚡  PROVISIONADOR DE SWITCH",
            font=("Segoe UI", 15, "bold"),
            text_color=COR_DESTAQUE,
        ).pack(side="left", padx=(20, 10), pady=11)

        # Menu Superior de Abas Flat (Estilo Software Premium)
        frame_nav_abas = ctk.CTkFrame(frame_titulo, fg_color="transparent")
        frame_nav_abas.pack(side="right", padx=16, pady=11)

        self.btn_tab_vault = ctk.CTkButton(
            frame_nav_abas,
            text="🗄️  Hosts Vault",
            command=lambda: self.selecionar_aba("vault"),
            font=("Segoe UI", 11, "bold"),
            height=36,
            corner_radius=8,
            cursor="hand2",
        )
        self.btn_tab_vault.pack(side="left", padx=5)

        self.btn_tab_serial = ctk.CTkButton(
            frame_nav_abas,
            text="⚡  Configuração Padrão - Serial",
            command=lambda: self.selecionar_aba("serial"),
            font=("Segoe UI", 11, "bold"),
            height=36,
            corner_radius=8,
            cursor="hand2",
        )
        self.btn_tab_serial.pack(side="left", padx=5)

        self.btn_tab_ssh = ctk.CTkButton(
            frame_nav_abas,
            text="🌐  Dashboard SSH e COM",
            command=lambda: self.selecionar_aba("ssh"),
            font=("Segoe UI", 11, "bold"),
            height=36,
            corner_radius=8,
            cursor="hand2",
        )
        self.btn_tab_ssh.pack(side="left", padx=5)

        self.btn_abrir_cme = ctk.CTkButton(
            frame_nav_abas,
            text="📞  Abrir Gerenciador CME",
            command=self._abrir_projeto_cme,
            font=("Segoe UI", 11, "bold"),
            height=36,
            corner_radius=8,
            cursor="hand2",
            fg_color="#21262d",
            hover_color="#30363d",
        )
        self.btn_abrir_cme.pack(side="left", padx=5)

        # TabView Principal (Container de Conteúdo das Abas)
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=COR_BG_ESCURO,
        )
        self.tabview.pack(fill="both", expand=True, padx=12, pady=(4, 10))

        aba_vault  = self.tabview.add("🗄️ Hosts Vault")
        aba_serial = self.tabview.add("⚡ Configuração Padrão - Serial")
        aba_ssh    = self.tabview.add("🌐 Dashboard SSH e COM")

        # Oculta o segmented button nativo para dar lugar às abas modernas flat
        self.tabview._segmented_button.grid_remove()
        self.tabview.rowconfigure(0, minsize=0)
        self.tabview.rowconfigure(1, minsize=0)
        self.tabview.rowconfigure(2, minsize=0)

        # --- ABA 0: HOSTS VAULT ---
        self._construir_hosts_vault(aba_vault)

        # --- ABA 1: CONFIGURAÇÃO PADRÃO (SERIAL) ---
        frame_corpo_serial = ctk.CTkFrame(aba_serial, fg_color=COR_BG_ESCURO)
        frame_corpo_serial.pack(fill="both", expand=True, padx=0, pady=4)

        frame_corpo_serial.columnconfigure(0, weight=0, minsize=270)
        frame_corpo_serial.columnconfigure(1, weight=0, minsize=320)
        frame_corpo_serial.columnconfigure(2, weight=1)
        frame_corpo_serial.rowconfigure(0, weight=1)

        # Coluna Esquerda
        col_esquerda = ctk.CTkFrame(frame_corpo_serial, fg_color=COR_FRAME, corner_radius=10, border_width=1, border_color=COR_FRAME_BORDA)
        col_esquerda.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        self._construir_painel_conexao(col_esquerda)

        # Coluna Centro
        col_centro = ctk.CTkFrame(frame_corpo_serial, fg_color=COR_FRAME, corner_radius=10, border_width=1, border_color=COR_FRAME_BORDA)
        col_centro.grid(row=0, column=1, sticky="nsew", padx=6, pady=0)
        self._construir_painel_configuracao(col_centro)

        # Coluna Direita (terminal)
        col_direita = ctk.CTkFrame(frame_corpo_serial, fg_color=COR_FRAME, corner_radius=10, border_width=1, border_color=COR_FRAME_BORDA)
        col_direita.grid(row=0, column=2, sticky="nsew", padx=(6, 0), pady=0)
        self._construir_painel_terminal(col_direita)

        # --- ABA 2: DASHBOARD SSH (DAY-1) ---
        self._construir_dashboard_ssh(aba_ssh)

        # Seleciona Hosts Vault por padrão atualizando os estilos das abas
        self.selecionar_aba("vault")

    def _abrir_projeto_cme(self) -> None:
        """Inicia a aplicação separada do Gerenciador Cisco CME em processo independente."""
        import subprocess
        cme_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CME")
        cme_script = os.path.join(cme_dir, "main.py")
        if os.path.exists(cme_script):
            subprocess.Popen([sys.executable, cme_script], cwd=cme_dir)
            self._log_terminal("Iniciando Gerenciador Cisco CME em janela separada...", "info")
        else:
            messagebox.showerror("Erro", f"Módulo do Gerenciador CME não encontrado em:\n{cme_script}")

    def selecionar_aba(self, nome_aba: str) -> None:
        """
        Alterna a aba ativa e atualiza os estilos dos botões de navegação superiores.
        Aplica estilo Flat com fundo transparente para abas inativas e destaque azul para a aba ativa.
        """
        mapa_abas = {
            "vault": "🗄️ Hosts Vault",
            "serial": "⚡ Configuração Padrão - Serial",
            "ssh": "🌐 Dashboard SSH e COM",
            "🗄️ Hosts Vault": "🗄️ Hosts Vault",
            "⚡ Configuração Padrão - Serial": "⚡ Configuração Padrão - Serial",
            "🌐 Dashboard SSH e COM": "🌐 Dashboard SSH e COM",
        }
        nome_real = mapa_abas.get(nome_aba, nome_aba)
        self.tabview.set(nome_real)

        if "vault" in nome_real.lower():
            chave = "vault"
        elif "serial" in nome_real.lower():
            chave = "serial"
        else:
            chave = "ssh"

        cor_ativa = ["#3B8ED0", "#1F6AA5"]
        cor_hover_ativa = ["#36719F", "#144870"]

        if self.btn_tab_vault:
            self.btn_tab_vault.configure(
                fg_color=cor_ativa if chave == "vault" else "transparent",
                text_color="white" if chave == "vault" else "gray70",
                hover_color=cor_hover_ativa if chave == "vault" else "gray25",
            )

        if self.btn_tab_serial:
            self.btn_tab_serial.configure(
                fg_color=cor_ativa if chave == "serial" else "transparent",
                text_color="white" if chave == "serial" else "gray70",
                hover_color=cor_hover_ativa if chave == "serial" else "gray25",
            )

        if self.btn_tab_ssh:
            self.btn_tab_ssh.configure(
                fg_color=cor_ativa if chave == "ssh" else "transparent",
                text_color="white" if chave == "ssh" else "gray70",
                hover_color=cor_hover_ativa if chave == "ssh" else "gray25",
            )

    # ------------------------------------------------------------------
    # Aba 0 — Hosts Vault (Estilo Termius)
    # ------------------------------------------------------------------

    def _construir_hosts_vault(self, pai: ctk.CTkFrame) -> None:
        """Constrói a tela de inventário de switches (Hosts Vault)."""
        frame_corpo = ctk.CTkFrame(pai, fg_color=COR_BG_ESCURO)
        frame_corpo.pack(fill="both", expand=True, padx=4, pady=4)

        # Barra Superior de Controles e Filtros
        frame_barra_topo = ctk.CTkFrame(
            frame_corpo,
            fg_color=COR_FRAME,
            corner_radius=10,
            border_width=1,
            border_color=COR_FRAME_BORDA,
            height=60,
        )
        frame_barra_topo.pack(fill="x", padx=6, pady=(2, 6))
        frame_barra_topo.pack_propagate(False)

        # Lado Esquerdo: Título e Contador
        frame_esq = ctk.CTkFrame(frame_barra_topo, fg_color="transparent")
        frame_esq.pack(side="left", fill="y", padx=14)

        ctk.CTkLabel(
            frame_esq,
            text="🗄️  Cofre de Switches",
            font=("Segoe UI", 14, "bold"),
            text_color="#ffffff",
        ).pack(side="left", pady=12)

        self._lbl_total_vault = ctk.CTkLabel(
            frame_esq,
            text="0 switches",
            font=("Segoe UI", 10, "bold"),
            text_color=COR_DESTAQUE,
            fg_color="#0d1117",
            corner_radius=6,
            padx=8,
            pady=3,
        )
        self._lbl_total_vault.pack(side="left", padx=10, pady=12)

        # Lado Direito: Filtros, Busca e Botão Novo Switch
        frame_dir = ctk.CTkFrame(frame_barra_topo, fg_color="transparent")
        frame_dir.pack(side="right", fill="y", padx=14)

        # Busca com KeyRelease
        entry_busca = ctk.CTkEntry(
            frame_dir,
            textvariable=self._var_vault_busca,
            placeholder_text="🔍 Buscar por nome, IP ou usuário...",
            width=240,
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
        )
        entry_busca.pack(side="left", padx=6, pady=12)
        entry_busca.bind("<KeyRelease>", lambda e: self._renderizar_cards_vault())

        # Dropdown de Grupos
        grupos_iniciais = ["Todos os Grupos"] + inventory.obter_grupos()
        self._combo_vault_grupos = ctk.CTkOptionMenu(
            frame_dir,
            variable=self._var_vault_grupo_filtro,
            values=grupos_iniciais,
            command=lambda v: self._renderizar_cards_vault(),
            width=140,
            fg_color="#0d1117",
            button_color=COR_BOTAO_PRIMARIO,
            button_hover_color=COR_BOTAO_HOVER,
            dropdown_fg_color="#0d1117",
            font=FONTE_LABEL,
        )
        self._combo_vault_grupos.pack(side="left", padx=5, pady=12)

        # Botão Ping Geral
        btn_ping = ctk.CTkButton(
            frame_dir,
            text="🔄  Ping Geral",
            command=lambda: self._disparar_ping_vault(),
            fg_color="#233549",
            hover_color="#304760",
            font=FONTE_LABEL_BOLD,
            width=110,
            height=32,
        )
        btn_ping.pack(side="left", padx=5, pady=12)

        # Botão Backup Massivo
        self.btn_backup_massivo = ctk.CTkButton(
            frame_dir,
            text="💾  Backup Massivo",
            command=self._abrir_modal_backup_massivo,
            fg_color="#1a4d36",
            hover_color="#246b4b",
            font=FONTE_LABEL_BOLD,
            width=135,
            height=32,
        )
        self.btn_backup_massivo.pack(side="left", padx=5, pady=12)

        # Botão + Novo Switch
        btn_novo_switch = ctk.CTkButton(
            frame_dir,
            text="➕  Novo Switch",
            command=self._abrir_modal_novo_host,
            fg_color="#1f6feb",
            hover_color="#388bfd",
            font=FONTE_LABEL_BOLD,
            width=120,
            height=32,
        )
        btn_novo_switch.pack(side="left", padx=(5, 0), pady=12)

        # Área de Rolagem para os Cards
        self._frame_scroll_vault = ctk.CTkScrollableFrame(
            frame_corpo,
            fg_color=COR_BG_ESCURO,
            corner_radius=8,
        )
        self._frame_scroll_vault.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # Renderização inicial dos cards
        self._renderizar_cards_vault()

    def _renderizar_cards_vault(self) -> None:
        """Renderiza os cartões de switches no grid da tela Hosts Vault."""
        if self._frame_scroll_vault is None:
            return

        # Limpa cards existentes e referências de status
        for child in self._frame_scroll_vault.winfo_children():
            child.destroy()
        self._vault_status_labels.clear()

        # Atualiza grupos no dropdown caso novos tenham sido criados
        todos_grupos = ["Todos os Grupos"] + inventory.obter_grupos()
        if self._combo_vault_grupos:
            self._combo_vault_grupos.configure(values=todos_grupos)

        # Obtém hosts do inventário
        todos_hosts = inventory.obter_hosts()
        termo_busca = (self._var_vault_busca.get() or "").lower().strip()
        grupo_filtro = self._var_vault_grupo_filtro.get()

        hosts_filtrados = []
        for h in todos_hosts:
            nome = str(h.get("name", "")).lower()
            ip = str(h.get("ip", "")).lower()
            user = str(h.get("user", "")).lower()
            grp = str(h.get("group", ""))

            if grupo_filtro != "Todos os Grupos" and grp != grupo_filtro:
                continue

            if termo_busca and (termo_busca not in nome and termo_busca not in ip and termo_busca not in user):
                continue

            hosts_filtrados.append(h)

        # Atualiza badge de total
        if self._lbl_total_vault:
            self._lbl_total_vault.configure(
                text=f"{len(hosts_filtrados)} de {len(todos_hosts)} switches"
            )

        if not hosts_filtrados:
            frame_vazio = ctk.CTkFrame(self._frame_scroll_vault, fg_color="transparent")
            frame_vazio.pack(expand=True, pady=60)
            ctk.CTkLabel(
                frame_vazio,
                text="🔍 Nenhum switch encontrado no Vault.",
                font=("Segoe UI", 14),
                text_color=COR_CINZA,
            ).pack(pady=(0, 8))
            ctk.CTkLabel(
                frame_vazio,
                text="Clique no botão '➕ Novo Switch' acima para adicionar equipamentos à sua lista.",
                font=FONTE_LABEL,
                text_color="#6e7681",
            ).pack()
            return

        # Configura Grid de 3 colunas responsivas
        NUM_COLUNAS = 3
        for col in range(NUM_COLUNAS):
            self._frame_scroll_vault.columnconfigure(col, weight=1, uniform="vault_grid")

        for idx, host in enumerate(hosts_filtrados):
            row = idx // NUM_COLUNAS
            col = idx % NUM_COLUNAS
            self._criar_card_switch(self._frame_scroll_vault, host, row, col)

        # Dispara verificação de ping assíncrona para os hosts exibidos
        self._disparar_ping_vault(hosts_filtrados)

    def _criar_card_switch(self, pai: ctk.CTkFrame, host: dict, row: int, col: int) -> None:
        """Cria um cartão individual para o switch no grid do Hosts Vault."""
        card = ctk.CTkFrame(
            pai,
            fg_color="#121824",
            corner_radius=10,
            border_width=1,
            border_color=COR_FRAME_BORDA,
            cursor="hand2",
        )
        card.grid(row=row, column=col, sticky="nsew", padx=8, pady=8)

        # Header do Card: Ícone + Nome e Botão de Excluir
        frame_header = ctk.CTkFrame(card, fg_color="transparent")
        frame_header.pack(fill="x", padx=12, pady=(12, 6))

        lbl_icone = ctk.CTkLabel(frame_header, text="🔀", font=("Segoe UI", 14))
        lbl_icone.pack(side="left", padx=(0, 6))

        lbl_nome = ctk.CTkLabel(
            frame_header,
            text=host.get("name", "Switch Sem Nome"),
            font=("Segoe UI", 12, "bold"),
            text_color="#ffffff",
            anchor="w",
        )
        lbl_nome.pack(side="left", fill="x", expand=True)

        btn_excluir = ctk.CTkButton(
            frame_header,
            text="✕",
            width=24,
            height=24,
            font=("Segoe UI", 11, "bold"),
            fg_color="transparent",
            text_color="#ff4757",
            hover_color="#3a1c22",
            command=lambda h=host: self._excluir_host_vault(h),
        )
        btn_excluir.pack(side="right")

        # Corpo do Card: String de Conexão
        user_str = host.get("user") or "admin"
        ip_str = host.get("ip") or "0.0.0.0"
        conn_str = f"ssh {user_str}@{ip_str}"

        frame_corpo = ctk.CTkFrame(card, fg_color="transparent")
        frame_corpo.pack(fill="x", padx=12, pady=(2, 10))

        lbl_conn = ctk.CTkLabel(
            frame_corpo,
            text=conn_str,
            font=("Consolas", 11),
            text_color=COR_DESTAQUE,
            anchor="w",
        )
        lbl_conn.pack(fill="x")

        # Rodapé do Card: Grupo e Status
        frame_footer = ctk.CTkFrame(card, fg_color="transparent")
        frame_footer.pack(fill="x", padx=12, pady=(0, 12))

        # Pill do Grupo
        grupo_str = host.get("group") or "Gerais"
        badge_grupo = ctk.CTkLabel(
            frame_footer,
            text=f"📁 {grupo_str}",
            font=("Segoe UI", 9, "bold"),
            text_color=COR_CINZA,
            fg_color="#1a263a",
            corner_radius=6,
            padx=6,
            pady=2,
        )
        badge_grupo.pack(side="left")

        # Status
        lbl_status = ctk.CTkLabel(
            frame_footer,
            text="● Testando...",
            font=("Segoe UI", 10, "bold"),
            text_color="#ffd32a",
        )
        lbl_status.pack(side="right")

        host_ip = host.get("ip", "")
        if host_ip:
            self._vault_status_labels[host_ip] = lbl_status

        # Interatividade (Hover + Clique)
        elementos_clicaveis = [card, frame_header, lbl_icone, lbl_nome, frame_corpo, lbl_conn, frame_footer, badge_grupo, lbl_status]

        def on_enter(e):
            card.configure(fg_color="#1a263a")

        def on_leave(e):
            card.configure(fg_color="#121824")

        def on_click(e, h=host):
            self.abrir_host(h)

        for el in elementos_clicaveis:
            el.bind("<Enter>", on_enter)
            el.bind("<Leave>", on_leave)
            el.bind("<Button-1>", on_click)

    def abrir_host(self, host: dict) -> None:
        """Transita para o Dashboard SSH, preenche os dados do switch e foca no campo de senha."""
        # 1. Muda para a aba Dashboard SSH e COM e atualiza o botão na barra superior
        self.selecionar_aba("ssh")

        # 2. Garante modo SSH selecionado
        self._var_modo_conexao.set("SSH (Rede IP)")
        if hasattr(self, "seg_modo_conexao"):
            self.seg_modo_conexao.set("SSH (Rede IP)")
        self._alternar_modo_conexao_dashboard("SSH (Rede IP)")

        # 3. Preenche IP e Usuário
        self._var_ssh_ip.set(host.get("ip", ""))
        self._var_ssh_user.set(host.get("user", "admin"))

        # 4. Limpa a senha (POLÍTICA ZERO-PERSISTENCE)
        self._var_ssh_pass.set("")

        # 5. Foca no campo de senha
        if self.entry_ssh_password:
            self.after(150, lambda: self.entry_ssh_password.focus_set())

    def _excluir_host_vault(self, host: dict) -> None:
        """Confirma e remove um switch do cofre."""
        nome = host.get("name", "Switch")
        ip = host.get("ip", "")
        if messagebox.askyesno(
            "Excluir Switch",
            f"Deseja realmente remover o switch '{nome}' ({ip}) do Hosts Vault?\n\nEsta ação não pode ser desfeita.",
            icon="warning",
        ):
            if inventory.remover_host(ip):
                self._renderizar_cards_vault()
            else:
                messagebox.showerror("Erro", f"Não foi possível remover o switch com IP {ip}.")

    # ------------------------------------------------------------------
    # NMS — Verificação de Conectividade ao Vivo (Ping)
    # ------------------------------------------------------------------

    def _ping_host(self, ip: str) -> bool:
        """Dispara ping leve (1 pacote, timeout 500ms) sem abrir janela de prompt."""
        if not ip:
            return False
        try:
            if os.name == "nt":
                cmd = ["ping", "-n", "1", "-w", "500", ip]
                flags = subprocess.CREATE_NO_WINDOW
            else:
                cmd = ["ping", "-c", "1", "-W", "1", ip]
                flags = 0
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
            )
            try:
                out = res.stdout.decode("cp850", errors="ignore").lower()
            except Exception:
                out = res.stdout.decode("utf-8", errors="ignore").lower()
            if any(err in out for err in ("inacess", "unreachable", "esgotado", "timed out", "falha", "failure")):
                return False
            return "ttl=" in out
        except Exception:
            return False

    def _disparar_ping_vault(self, hosts: Optional[List[dict]] = None) -> None:
        """Inicia verificação de ping em background para os hosts do Vault."""
        if hosts is None:
            hosts = inventory.obter_hosts()
        self._ping_gen_id += 1
        gen_id = self._ping_gen_id

        # Coloca todos em '● Testando...' na UI
        for h in hosts:
            ip = h.get("ip", "")
            lbl = self._vault_status_labels.get(ip)
            if lbl and lbl.winfo_exists():
                lbl.configure(text="● Testando...", text_color="#ffd32a")

        t = threading.Thread(
            target=self._worker_ping_vault,
            args=(list(hosts), gen_id),
            daemon=True,
        )
        t.start()

    def _worker_ping_vault(self, hosts_list: List[dict], gen_id: int) -> None:
        """Thread worker que testa cada switch e atualiza a UI via self.after."""
        for h in hosts_list:
            if gen_id != self._ping_gen_id:
                return  # Abortado por nova busca/filtro
            ip = h.get("ip", "")
            if not ip:
                continue
            online = self._ping_host(ip)
            if gen_id == self._ping_gen_id:
                try:
                    if self.winfo_exists():
                        self.after(0, self._atualizar_ping_status_ui, ip, online, gen_id)
                except Exception:
                    pass

    def _atualizar_ping_status_ui(self, ip: str, is_online: bool, gen_id: int) -> None:
        """Atualiza o label de status de um switch na tela do Vault (executado na thread principal)."""
        if gen_id != self._ping_gen_id:
            return
        lbl = self._vault_status_labels.get(ip)
        if lbl and lbl.winfo_exists():
            if is_online:
                lbl.configure(text="● Online", text_color="#00ff88")
            else:
                lbl.configure(text="● Offline", text_color="#ff4757")

    # ------------------------------------------------------------------
    # NMS — Backup Massivo de Configurações (Config Archiver)
    # ------------------------------------------------------------------

    def _abrir_modal_backup_massivo(self) -> None:
        """Abre janela modal para arquivar configurações (running-config) em massa."""
        modal = ctk.CTkToplevel(self)
        modal.title("💾  Backup Massivo de Configurações (Config Archiver)")
        modal.geometry("540x620")
        modal.minsize(500, 520)
        modal.configure(fg_color=COR_BG_ESCURO)
        modal.transient(self)
        modal.grab_set()

        # Centraliza modal sobre a janela principal
        modal.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 270
        y = self.winfo_y() + (self.winfo_height() // 2) - 310
        modal.geometry(f"+{max(0, x)}+{max(0, y)}")

        # Cabeçalho
        ctk.CTkLabel(
            modal,
            text="💾  Config Archiver — Backup Massivo",
            font=("Segoe UI", 14, "bold"),
            text_color=COR_DESTAQUE,
        ).pack(anchor="w", padx=20, pady=(16, 2))

        ctk.CTkLabel(
            modal,
            text="Coleta automaticamente o 'show running-config' dos switches selecionados via SSH.",
            font=("Segoe UI", 10),
            text_color=COR_CINZA,
        ).pack(anchor="w", padx=20, pady=(0, 8))

        ctk.CTkFrame(modal, height=1, fg_color=COR_FRAME_BORDA).pack(fill="x", padx=20, pady=(0, 10))

        # Container de Formulário
        frame_form = ctk.CTkFrame(modal, fg_color="transparent")
        frame_form.pack(fill="x", padx=20, pady=(0, 10))

        # Grupo Alvo
        grupos_disponiveis = ["Todos os Grupos"] + inventory.obter_grupos()
        ctk.CTkLabel(frame_form, text="Grupo Alvo:", font=FONTE_LABEL_BOLD, text_color="#ffffff").pack(anchor="w", pady=(0, 2))
        combo_grupo = ctk.CTkComboBox(
            frame_form,
            values=grupos_disponiveis,
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            dropdown_fg_color="#0d1117",
            button_color=COR_BOTAO_PRIMARIO,
            button_hover_color=COR_BOTAO_HOVER,
            height=32,
        )
        combo_grupo.set("Todos os Grupos")
        combo_grupo.pack(fill="x", pady=(0, 10))

        # Senha SSH (Zero-Persistence: Memória RAM volátil apenas)
        ctk.CTkLabel(
            frame_form,
            text="Senha SSH (Políticas de Domínio/Switch — apenas em RAM):",
            font=FONTE_LABEL_BOLD,
            text_color="#ffffff",
        ).pack(anchor="w", pady=(0, 2))
        entry_senha = ctk.CTkEntry(
            frame_form,
            placeholder_text="Digite a senha de acesso aos switches...",
            show="*",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            height=32,
        )
        entry_senha.pack(fill="x", pady=(0, 10))

        # Pasta de Destino
        ctk.CTkLabel(frame_form, text="Pasta de Destino para os Arquivos .txt:", font=FONTE_LABEL_BOLD, text_color="#ffffff").pack(anchor="w", pady=(0, 2))
        frame_pasta = ctk.CTkFrame(frame_form, fg_color="transparent")
        frame_pasta.pack(fill="x", pady=(0, 12))

        var_pasta = ctk.StringVar(value=os.path.join(os.getcwd(), "backups"))
        entry_pasta = ctk.CTkEntry(
            frame_pasta,
            textvariable=var_pasta,
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            height=32,
        )
        entry_pasta.pack(side="left", fill="x", expand=True, padx=(0, 6))

        def selecionar_pasta():
            p = filedialog.askdirectory(parent=modal, title="Selecione a Pasta de Destino")
            if p:
                var_pasta.set(p)

        ctk.CTkButton(
            frame_pasta,
            text="📁 Navegar",
            width=85,
            height=32,
            command=selecionar_pasta,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
            font=FONTE_LABEL,
        ).pack(side="right")

        # Barra de Progresso e Indicador
        frame_progresso = ctk.CTkFrame(modal, fg_color="transparent")
        frame_progresso.pack(fill="x", padx=20, pady=(0, 6))

        progress_bar = ctk.CTkProgressBar(frame_progresso, height=12)
        progress_bar.set(0.0)
        progress_bar.pack(fill="x", pady=(0, 4))

        lbl_progresso = ctk.CTkLabel(
            frame_progresso,
            text="Aguardando início...",
            font=("Segoe UI", 10),
            text_color=COR_CINZA,
        )
        lbl_progresso.pack(anchor="w")

        # Log Textbox de Acompanhamento em Tempo Real
        ctk.CTkLabel(modal, text="Log de Execução:", font=FONTE_LABEL_BOLD, text_color="#ffffff").pack(anchor="w", padx=20, pady=(0, 2))
        log_box = ctk.CTkTextbox(
            modal,
            font=("Consolas", 10),
            fg_color="#0d1117",
            text_color="#00ff88",
            border_color=COR_FRAME_BORDA,
            border_width=1,
            height=130,
            wrap="none",
        )
        log_box.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # Botões de Ação
        frame_botoes = ctk.CTkFrame(modal, fg_color="transparent")
        frame_botoes.pack(fill="x", padx=20, pady=(0, 16))

        btn_iniciar = ctk.CTkButton(
            frame_botoes,
            text="🚀  Iniciar Backup",
            font=FONTE_LABEL_BOLD,
            fg_color="#1f6feb",
            hover_color="#388bfd",
            height=36,
        )
        btn_iniciar.pack(side="right", padx=(8, 0))

        btn_fechar = ctk.CTkButton(
            frame_botoes,
            text="Fechar",
            command=modal.destroy,
            fg_color="#3a3a3a",
            hover_color="#555",
            font=FONTE_LABEL,
            height=36,
            width=90,
        )
        btn_fechar.pack(side="right")

        def executar_backup():
            grupo_sel = combo_grupo.get()
            senha = entry_senha.get()
            pasta = var_pasta.get().strip()

            if not senha:
                messagebox.showerror("Senha Obrigatória", "Por favor, digite a senha SSH.", parent=modal)
                entry_senha.focus_set()
                return

            if not pasta:
                messagebox.showerror("Pasta Obrigatória", "Por favor, selecione a pasta de destino.", parent=modal)
                return

            os.makedirs(pasta, exist_ok=True)

            todos_hosts = inventory.obter_hosts()
            if grupo_sel != "Todos os Grupos":
                hosts_alvo = [h for h in todos_hosts if h.get("group") == grupo_sel]
            else:
                hosts_alvo = todos_hosts

            if not hosts_alvo:
                messagebox.showinfo("Nenhum Switch", f"Nenhum switch encontrado para o grupo '{grupo_sel}'.", parent=modal)
                return

            btn_iniciar.configure(state="disabled", text="⏳ Executando...")
            btn_fechar.configure(state="disabled")

            threading.Thread(
                target=self._worker_backup_massivo,
                args=(hosts_alvo, senha, pasta, modal, progress_bar, lbl_progresso, log_box, btn_iniciar, btn_fechar),
                daemon=True,
            ).start()

        btn_iniciar.configure(command=executar_backup)

    def _worker_backup_massivo(
        self,
        hosts_alvo: List[dict],
        senha: str,
        pasta_destino: str,
        modal: ctk.CTkToplevel,
        progress_bar: ctk.CTkProgressBar,
        lbl_progresso: ctk.CTkLabel,
        log_box: ctk.CTkTextbox,
        btn_iniciar: ctk.CTkButton,
        btn_fechar: ctk.CTkButton,
    ) -> None:
        """Executa em thread separada a coleta de running-config para cada switch."""
        total = len(hosts_alvo)
        sucessos = 0
        falhas = 0

        def log_msg(texto: str):
            if modal.winfo_exists() and log_box.winfo_exists():
                log_box.insert("end", texto + "\n")
                log_box.see("end")

        def atualizar_progresso(idx: int, texto: str):
            if modal.winfo_exists() and progress_bar.winfo_exists():
                frac = idx / total if total > 0 else 1.0
                progress_bar.set(frac)
                lbl_progresso.configure(text=texto)

        data_hoje = datetime.now().strftime("%Y-%m-%d")
        self.after(0, log_msg, f"=== INICIANDO BACKUP MASSIVO: {total} SWITCH(ES) ===")
        self.after(0, log_msg, f"Destino: {pasta_destino}\n")

        for idx, h in enumerate(hosts_alvo):
            nome = h.get("name", "Switch")
            ip = h.get("ip", "")
            user = h.get("user", "admin")

            self.after(0, atualizar_progresso, idx, f"Processando [{idx+1}/{total}]: {nome} ({ip})...")
            self.after(0, log_msg, f"[{idx+1}/{total}] Conectando a {nome} ({ip})...")

            if not ip:
                self.after(0, log_msg, f"   [ERRO] IP inválido ou não configurado.")
                falhas += 1
                continue

            try:
                client = CiscoSSHClient(host=ip, username=user, password=senha, timeout=10)
                client.conectar()
                self.after(0, log_msg, f"   Conectado! Coletando 'show running-config'...")
                config_texto = client.executar_comando("show running-config")
                client.desconectar()

                # Sanitiza nome do arquivo
                safe_name = re.sub(r"[^\w\-]", "_", nome)
                nome_arquivo = f"{safe_name}_{data_hoje}.txt"
                caminho_arquivo = os.path.join(pasta_destino, nome_arquivo)

                with open(caminho_arquivo, "w", encoding="utf-8") as f:
                    f.write(config_texto)

                self.after(0, log_msg, f"   [OK] Salvo em: {nome_arquivo}")
                sucessos += 1
            except Exception as e:
                self.after(0, log_msg, f"   [FALHA] Erro ao coletar de {nome} ({ip}): {e}")
                falhas += 1

        self.after(0, atualizar_progresso, total, f"Concluído! {sucessos} sucesso(s), {falhas} falha(s).")
        self.after(0, log_msg, f"\n=== BACKUP FINALIZADO ===")
        self.after(0, log_msg, f"Total: {total} | Sucessos: {sucessos} | Falhas: {falhas}")

        def finalizar():
            if btn_iniciar.winfo_exists():
                btn_iniciar.configure(state="normal", text="🚀  Iniciar Backup")
            if btn_fechar.winfo_exists():
                btn_fechar.configure(state="normal")
            messagebox.showinfo(
                "Backup Concluído",
                f"Backup massivo finalizado!\n\n"
                f"• Sucessos: {sucessos}\n"
                f"• Falhas: {falhas}\n"
                f"• Destino: {pasta_destino}",
                parent=modal if modal.winfo_exists() else None,
            )

        self.after(0, finalizar)

    def _abrir_modal_novo_host(self) -> None:
        """Abre janela modal para cadastrar novo switch no inventário."""
        modal = ctk.CTkToplevel(self)
        modal.title("➕ Cadastrar Novo Switch no Vault")
        modal.geometry("480x580")
        modal.minsize(450, 480)
        modal.configure(fg_color=COR_BG_ESCURO)
        modal.transient(self)
        modal.grab_set()

        # Centraliza modal sobre a janela principal
        modal.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 240
        y = self.winfo_y() + (self.winfo_height() // 2) - 290
        modal.geometry(f"+{max(0, x)}+{max(0, y)}")

        # Cabeçalho Fixo
        ctk.CTkLabel(
            modal,
            text="⚡  Novo Switch no Inventário",
            font=("Segoe UI", 14, "bold"),
            text_color=COR_DESTAQUE,
        ).pack(anchor="w", padx=20, pady=(16, 2))

        ctk.CTkLabel(
            modal,
            text="🔒 Arquitetura Zero-Persistence: Senhas NUNCA são salvas em disco.",
            font=("Segoe UI", 9),
            text_color=COR_VERDE,
        ).pack(anchor="w", padx=20, pady=(0, 10))

        ctk.CTkFrame(modal, height=1, fg_color=COR_FRAME_BORDA).pack(fill="x", padx=20, pady=(0, 10))

        # Conteúdo em Frame com Rolagem para Garantir Responsividade em Qualquer Resolução/DPI
        scroll_modal = ctk.CTkScrollableFrame(modal, fg_color="transparent")
        scroll_modal.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        frame_campos = ctk.CTkFrame(scroll_modal, fg_color="transparent")
        frame_campos.pack(fill="x", padx=10, pady=(4, 0))

        # Campo Nome
        ctk.CTkLabel(frame_campos, text="Nome do Switch / Identificação:", font=FONTE_LABEL_BOLD, text_color="#ffffff").pack(anchor="w", pady=(0, 2))
        entry_nome = ctk.CTkEntry(
            frame_campos,
            placeholder_text="ex: SWITCH-CORE-01",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            height=32,
        )
        entry_nome.pack(fill="x", pady=(0, 10))

        # Campo IP
        ctk.CTkLabel(frame_campos, text="Endereço IP de Gerência:", font=FONTE_LABEL_BOLD, text_color="#ffffff").pack(anchor="w", pady=(0, 2))
        entry_ip = ctk.CTkEntry(
            frame_campos,
            placeholder_text="ex: 192.0.2.10",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            height=32,
        )
        entry_ip.pack(fill="x", pady=(0, 10))

        # Campo Usuário SSH
        ctk.CTkLabel(frame_campos, text="Usuário SSH Padrão:", font=FONTE_LABEL_BOLD, text_color="#ffffff").pack(anchor="w", pady=(0, 2))
        entry_user = ctk.CTkEntry(
            frame_campos,
            placeholder_text="ex: admin",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            height=32,
        )
        entry_user.insert(0, "admin")
        entry_user.pack(fill="x", pady=(0, 10))

        # Campo Grupo
        grupos_disponiveis = inventory.obter_grupos()
        if not grupos_disponiveis:
            grupos_disponiveis = ["Gerais"]

        ctk.CTkLabel(frame_campos, text="Grupo / Localidade:", font=FONTE_LABEL_BOLD, text_color="#ffffff").pack(anchor="w", pady=(0, 2))
        combo_grupo = ctk.CTkComboBox(
            frame_campos,
            values=grupos_disponiveis,
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            dropdown_fg_color="#0d1117",
            button_color=COR_BOTAO_PRIMARIO,
            button_hover_color=COR_BOTAO_HOVER,
            height=32,
        )
        combo_grupo.set(grupos_disponiveis[0])
        combo_grupo.pack(fill="x", pady=(0, 16))

        # Botões Salvar e Cancelar
        frame_botoes = ctk.CTkFrame(scroll_modal, fg_color="transparent")
        frame_botoes.pack(fill="x", padx=10, pady=(10, 16))

        def salvar():
            nome = entry_nome.get().strip()
            ip = entry_ip.get().strip()
            user = entry_user.get().strip() or "admin"
            grupo = combo_grupo.get().strip() or "Gerais"

            if not nome:
                messagebox.showerror("Campo Obrigatório", "Por favor, informe o Nome do Switch.", parent=modal)
                entry_nome.focus_set()
                return

            if not ip:
                messagebox.showerror("Campo Obrigatório", "Por favor, informe o Endereço IP.", parent=modal)
                entry_ip.focus_set()
                return

            sucesso = inventory.adicionar_host(nome, ip, user, grupo)
            if sucesso:
                self._renderizar_cards_vault()
                modal.destroy()
            else:
                messagebox.showerror("Erro ao Salvar", "Não foi possível salvar o switch no arquivo de inventário.", parent=modal)

        btn_salvar = ctk.CTkButton(
            frame_botoes,
            text="💾 Salvar Switch",
            command=salvar,
            fg_color="#1f6feb",
            hover_color="#388bfd",
            font=FONTE_LABEL_BOLD,
            height=36,
        )
        btn_salvar.pack(side="right", padx=(8, 0))

        btn_cancelar = ctk.CTkButton(
            frame_botoes,
            text="Cancelar",
            command=modal.destroy,
            fg_color="#3a3a3a",
            hover_color="#555",
            font=FONTE_LABEL,
            height=36,
            width=90,
        )
        btn_cancelar.pack(side="right")

        # Enter para avançar/salvar no formulário
        entry_nome.bind("<Return>", lambda e: entry_ip.focus_set())
        entry_ip.bind("<Return>", lambda e: entry_user.focus_set())
        entry_user.bind("<Return>", lambda e: salvar())
        entry_nome.focus_set()

    # ------------------------------------------------------------------
    # Painel Esquerdo — Conexão Serial + Hardware
    # ------------------------------------------------------------------

    def _construir_painel_conexao(self, pai: ctk.CTkFrame) -> None:
        """Painel de seleção de porta e hardware."""

        def secao(texto: str) -> None:
            ctk.CTkLabel(pai, text=texto, font=FONTE_TITULO, text_color=COR_DESTAQUE).pack(
                anchor="w", padx=16, pady=(14, 4)
            )
            ctk.CTkFrame(pai, height=1, fg_color=COR_FRAME_BORDA).pack(fill="x", padx=16, pady=(0, 10))

        def linha_label(label: str) -> None:
            ctk.CTkLabel(pai, text=label, font=FONTE_LABEL, text_color=COR_CINZA).pack(
                anchor="w", padx=16, pady=(2, 0)
            )

        # --- Seção: Porta Serial ---
        secao("🔌  Conexão Serial")

        linha_label("Porta COM")
        frame_porta = ctk.CTkFrame(pai, fg_color="transparent")
        frame_porta.pack(fill="x", padx=16, pady=(0, 6))

        self._combo_porta = ctk.CTkComboBox(
            frame_porta,
            variable=self._var_porta,
            width=130,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            button_color=COR_BOTAO_PRIMARIO,
            font=FONTE_LABEL,
            dropdown_fg_color="#0d1117",
        )
        self._combo_porta.pack(side="left")

        ctk.CTkButton(
            frame_porta,
            text="🔄",
            width=36,
            command=self._atualizar_portas,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
        ).pack(side="left", padx=(6, 0))

        linha_label("Baudrate")
        self._combo_baud = ctk.CTkComboBox(
            pai,
            variable=self._var_baudrate,
            values=["9600", "19200", "38400", "57600", "115200"],
            width=180,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            button_color=COR_BOTAO_PRIMARIO,
            font=FONTE_LABEL,
            dropdown_fg_color="#0d1117",
        )
        self._combo_baud.pack(anchor="w", padx=16, pady=(0, 10))

        # --- Indicador de status ---
        frame_status = ctk.CTkFrame(pai, fg_color="transparent")
        frame_status.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(frame_status, text="Status:", font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(side="left")
        self._lbl_status = ctk.CTkLabel(
            frame_status,
            text="● Desconectado",
            font=FONTE_LABEL_BOLD,
            text_color=COR_VERMELHO,
        )
        self._lbl_status.pack(side="left", padx=8)

        # --- Botões Conectar / Desconectar ---
        frame_btns = ctk.CTkFrame(pai, fg_color="transparent")
        frame_btns.pack(fill="x", padx=16, pady=(0, 10))

        self._btn_conectar = ctk.CTkButton(
            frame_btns,
            text="Conectar",
            command=self._conectar,
            fg_color="#1a6b3a",
            hover_color="#248a4c",
            font=FONTE_LABEL_BOLD,
            width=100,
        )
        self._btn_conectar.pack(side="left")

        self._btn_desconectar = ctk.CTkButton(
            frame_btns,
            text="Desconectar",
            command=self._desconectar,
            fg_color="#6b1a1a",
            hover_color="#8a2424",
            font=FONTE_LABEL_BOLD,
            width=110,
            state="disabled",
        )
        self._btn_desconectar.pack(side="left", padx=(8, 0))

        # --- Seção: Credenciais / Auto-Login Console ---
        secao("🔑  Acesso Console (Login)")

        linha_label("Username")
        self.entry_serial_user = ctk.CTkEntry(
            pai,
            placeholder_text="ex: admin",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            text_color="#ffffff",
            width=230,
        )
        self.entry_serial_user.insert(0, "admin")
        self.entry_serial_user.pack(anchor="w", padx=16, pady=(0, 6))

        linha_label("Password")
        self.entry_serial_pass = ctk.CTkEntry(
            pai,
            placeholder_text="Senha do console",
            show="*",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            text_color="#ffffff",
            width=230,
        )
        self.entry_serial_pass.pack(anchor="w", padx=16, pady=(0, 8))

        frame_login_btns = ctk.CTkFrame(pai, fg_color="transparent")
        frame_login_btns.pack(fill="x", padx=16, pady=(0, 10))

        self.btn_auto_login = ctk.CTkButton(
            frame_login_btns,
            text="🔑 Auto-Login",
            command=self.executar_auto_login_serial,
            fg_color="#1f538d",
            hover_color="#14375e",
            font=FONTE_LABEL_BOLD,
            width=112,
        )
        self.btn_auto_login.pack(side="left")

        self.btn_logout_console = ctk.CTkButton(
            frame_login_btns,
            text="🔒 Logout",
            command=self.executar_logout_serial,
            fg_color="#4a4a4a",
            hover_color="#5a5a5a",
            font=FONTE_LABEL_BOLD,
            width=110,
        )
        self.btn_logout_console.pack(side="left", padx=(8, 0))

        # --- Seção: Hardware ---
        secao("🖥️  Hardware")

        linha_label("Modelo do Switch")
        self._combo_modelo = ctk.CTkComboBox(
            pai,
            variable=self._var_modelo,
            values=MODELOS_DISPONIVEIS,
            width=230,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            button_color=COR_BOTAO_PRIMARIO,
            font=FONTE_LABEL,
            dropdown_fg_color="#0d1117",
        )
        self._combo_modelo.pack(anchor="w", padx=16, pady=(0, 12))

        # --- Rodapé informativo ---
        ctk.CTkLabel(
            pai,
            text="Cabos compatíveis:\nRS-232 → DB9 / USB-Serial\nRJ-45 Console → USB",
            font=("Segoe UI", 9),
            text_color=COR_CINZA,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(10, 0))

    # ------------------------------------------------------------------
    # Painel Central — Parâmetros de Configuração
    # ------------------------------------------------------------------

    def _construir_painel_configuracao(self, pai: ctk.CTkFrame) -> None:
        """Painel de formulário com os parâmetros Day-0."""

        # Scroll interno para acomodar muitos campos
        scroll = ctk.CTkScrollableFrame(pai, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        def secao(texto: str) -> None:
            ctk.CTkLabel(scroll, text=texto, font=FONTE_TITULO, text_color=COR_DESTAQUE).pack(
                anchor="w", padx=12, pady=(14, 4)
            )
            ctk.CTkFrame(scroll, height=1, fg_color=COR_FRAME_BORDA).pack(fill="x", padx=12, pady=(0, 8))

        def campo(label: str, variavel: ctk.StringVar, placeholder: str = "", oculto: bool = False) -> ctk.CTkEntry:
            ctk.CTkLabel(scroll, text=label, font=FONTE_LABEL, text_color=COR_CINZA).pack(
                anchor="w", padx=12, pady=(0, 2)
            )
            entry = ctk.CTkEntry(
                scroll,
                textvariable=variavel,
                placeholder_text=placeholder,
                show="*" if oculto else "",
                fg_color="#0d1117",
                border_color=COR_FRAME_BORDA,
                font=FONTE_LABEL,
                width=280,
            )
            entry.pack(anchor="w", padx=12, pady=(0, 8))
            return entry

        # --- Identificação ---
        secao("🏷️  Identificação")
        campo("Hostname do Switch", self._var_hostname, placeholder="ex: SW-ACESSO-01")
        campo("Senha de Enable (secret)", self._var_secret, placeholder="Senha forte", oculto=True)

        # --- Gerência IP ---
        secao("🌐  Gerência IP")
        campo("VLAN de Gerência", self._var_vlan_mgmt, placeholder="1")
        campo("Endereço IP de Gerência", self._var_ip_mgmt, placeholder="ex: 192.168.1.10")
        campo("Máscara de Sub-rede", self._var_mascara, placeholder="ex: 255.255.255.0")
        campo("Default Gateway", self._var_gateway, placeholder="ex: 192.168.1.1")

        # --- VLANs adicionais ---
        secao("📋  VLANs Adicionais (opcional)")

        frame_vlan_add = ctk.CTkFrame(scroll, fg_color="transparent")
        frame_vlan_add.pack(fill="x", padx=12, pady=(0, 6))

        ctk.CTkLabel(frame_vlan_add, text="ID", font=FONTE_LABEL, text_color=COR_CINZA, width=50).pack(side="left")
        ctk.CTkEntry(
            frame_vlan_add,
            textvariable=self._var_vlan_id,
            placeholder_text="ex: 10",
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            font=FONTE_LABEL,
            width=70,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(frame_vlan_add, text="Nome", font=FONTE_LABEL, text_color=COR_CINZA).pack(side="left")
        ctk.CTkEntry(
            frame_vlan_add,
            textvariable=self._var_vlan_nome,
            placeholder_text="ex: DADOS",
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            font=FONTE_LABEL,
            width=100,
        ).pack(side="left", padx=(4, 8))

        ctk.CTkButton(
            frame_vlan_add,
            text="+ Add",
            width=55,
            command=self._adicionar_vlan,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
            font=FONTE_LABEL,
        ).pack(side="left")

        # Lista de VLANs adicionadas
        self._frame_lista_vlans = ctk.CTkScrollableFrame(
            scroll, height=90, fg_color="#0d1117", corner_radius=6
        )
        self._frame_lista_vlans.pack(fill="x", padx=12, pady=(0, 10))
        self._atualizar_lista_vlans()

        # --- Botões de Ação / Execução ---
        secao("🚀  Execução")

        self.btn_aplicar_padrao = ctk.CTkButton(
            scroll,
            text="🚀  Aplicar Configuração Padrão",
            command=self.iniciar_provisionamento,
            fg_color="#006400",          # verde escuro para ação recomendada
            hover_color="#008000",
            font=("Segoe UI", 12, "bold"),
            height=46,
            width=280,
        )
        self.btn_aplicar_padrao.pack(padx=12, pady=(0, 8))

        self.btn_resetar = ctk.CTkButton(
            scroll,
            text="⚠️  Resetar Switch (Padrão de Fábrica)",
            command=self.iniciar_reset_switch,
            fg_color="#8B0000",          # vermelho escuro para operação destrutiva/reset
            hover_color="#A52A2A",
            font=("Segoe UI", 12, "bold"),
            height=42,
            width=280,
        )
        self.btn_resetar.pack(padx=12, pady=(0, 16))

    # ------------------------------------------------------------------
    # Painel Direito — Terminal de Logs
    # ------------------------------------------------------------------

    def _construir_painel_terminal(self, pai: ctk.CTkFrame) -> None:
        """Terminal de logs em tempo real."""

        # Cabeçalho do painel
        frame_cabec = ctk.CTkFrame(pai, fg_color="transparent", height=40)
        frame_cabec.pack(fill="x", padx=12, pady=(10, 4))
        frame_cabec.pack_propagate(False)

        ctk.CTkLabel(
            frame_cabec,
            text="📟  Terminal Serial / Log",
            font=FONTE_TITULO,
            text_color=COR_DESTAQUE,
        ).pack(side="left", anchor="w")

        # Botões de ação do log
        frame_btn_log = ctk.CTkFrame(frame_cabec, fg_color="transparent")
        frame_btn_log.pack(side="right")

        ctk.CTkButton(
            frame_btn_log,
            text="🗑 Limpar",
            width=80,
            height=28,
            command=self._limpar_log,
            fg_color="#3a3a3a",
            hover_color="#555",
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            frame_btn_log,
            text="💾 Exportar",
            width=90,
            height=28,
            command=self._exportar_log,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
            font=("Segoe UI", 10),
        ).pack(side="left")

        # Separador
        ctk.CTkFrame(pai, height=1, fg_color=COR_FRAME_BORDA).pack(fill="x", padx=12, pady=(0, 6))

        # Área de texto do terminal (tk.Text para controle de tags de cor e emulação PuTTY)
        frame_texto = ctk.CTkFrame(pai, fg_color=COR_TERMINAL_BG, corner_radius=6)
        frame_texto.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._terminal = tk.Text(
            frame_texto,
            bg=COR_TERMINAL_BG,
            fg="#e0e0e0",
            insertbackground=COR_DESTAQUE,
            font=FONTE_TERMINAL,
            wrap="word",
            state="disabled",
            relief="flat",
            padx=8,
            pady=6,
            cursor="xterm",
        )
        scrollbar = tk.Scrollbar(frame_texto, command=self._terminal.yview, bg=COR_FRAME)
        self._terminal.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self._terminal.pack(fill="both", expand=True)

        self.textbox_terminal = self._terminal

        # Torna o terminal focável e captura teclas diretamente (estilo PuTTY)
        self._terminal.bind("<Key>", self._on_terminal_keypress)
        # Impede que o utilizador mova o cursor com o mouse e estrague o texto
        self._terminal.bind("<Button-1>", lambda e: self._terminal.focus_set() or "break")
        self._terminal.bind("<Control-v>", self._on_terminal_paste)

        # Tags de cor para diferentes tipos de mensagem
        self._terminal.tag_config("info",     foreground="#c0c0c0")
        self._terminal.tag_config("aviso",    foreground="#f39c12", font=("Consolas", 10, "bold"))
        self._terminal.tag_config("enviado",  foreground=COR_AMARELO)
        self._terminal.tag_config("recebido", foreground="#80d4ff")
        self._terminal.tag_config("erro",     foreground=COR_VERMELHO)
        self._terminal.tag_config("sucesso",  foreground=COR_VERDE)
        self._terminal.tag_config("titulo",   foreground=COR_DESTAQUE, font=("Consolas", 11, "bold"))
        self._terminal.tag_config("tempo",    foreground="#555")

        # Mensagem de boas-vindas
        self._log_terminal("=" * 56, "titulo")
        self._log_terminal("  PROVISIONADOR DE SWITCH", "titulo")
        self._log_terminal("  Aguardando conexão serial...", "info")
        self._log_terminal("=" * 56, "titulo")

    # ------------------------------------------------------------------
    # Lógica de Conexão
    # ------------------------------------------------------------------

    def _atualizar_portas(self) -> None:
        """Escaneia e popula o combobox de portas COM disponíveis."""
        portas = listar_portas()
        if portas:
            self._combo_porta.configure(values=portas)
            self._var_porta.set(portas[0])
            self._log_terminal(f"Portas detectadas: {', '.join(portas)}", "info")
        else:
            self._combo_porta.configure(values=["Nenhuma porta"])
            self._var_porta.set("Nenhuma porta")
            self._log_terminal("Nenhuma porta COM detectada.", "erro")

    def _conectar(self) -> None:
        """Tenta abrir a conexão serial com os parâmetros selecionados."""
        porta   = self._var_porta.get()
        baud_str = self._var_baudrate.get()

        if not porta or porta == "Nenhuma porta":
            messagebox.showerror("Erro", "Selecione uma porta COM válida.")
            return

        try:
            baudrate = int(baud_str)
        except ValueError:
            messagebox.showerror("Erro", f"Baudrate inválido: {baud_str}")
            return

        try:
            self._serial_client = CiscoSerial(
                porta=porta,
                baudrate=baudrate,
                callback_log=self._callback_log_serial,
            )
            self._serial_client.conectar()
            self.serial_conn = self._serial_client.serial_conn

            # 1. Envia imediatamente \r\n para forçar o switch a retornar o prompt atual
            if self.serial_conn and self.serial_conn.is_open:
                self.serial_conn.write(b"\r\n")
                self.serial_conn.flush()

            # 2. Inicia thread dedicada à escuta contínua da porta serial
            threading.Thread(target=self.leitura_continua_serial, daemon=True).start()

            self._atualizar_status_ui(conectado=True)
        except Exception as exc:
            messagebox.showerror("Falha na Conexão", str(exc))
            self._atualizar_status_ui(conectado=False)

    def _desconectar(self) -> None:
        """Fecha a conexão serial ativa de forma limpa e segura."""
        self.esperando_resposta = False
        self._provisionamento_ativo = False
        if self._serial_client:
            self._serial_client.desconectar()
            self._serial_client = None
        self.serial_conn = None
        self._atualizar_status_ui(conectado=False)

    def leitura_continua_serial(self) -> None:
        """
        Thread dedicada em background para escuta ininterrupta da porta serial.
        Lê bytes que chegam do switch em tempo real e os encaminha
        para o terminal da GUI sem timestamps do sistema, preservando
        o layout idêntico ao de um terminal PuTTY.
        """
        while self.serial_conn and self.serial_conn.is_open:
            try:
                if self.serial_conn.in_waiting > 0:
                    dados = self.serial_conn.read(self.serial_conn.in_waiting).decode("utf-8", errors="ignore")
                    if dados:
                        # Injeta no Textbox da GUI de forma thread-safe
                        self.after(0, self._inserir_dados_brutos_terminal, dados)
                        # Acumula na captura do dashboard se estiver ativa
                        if getattr(self, "_captura_serial_ativa", False):
                            self._buffer_captura_serial += dados
                        # Acumula na captura do provisionamento se estiver ativa
                        if getattr(self, "_provisionamento_ativo", False):
                            self._buffer_provisionamento += dados
            except (serial.SerialException, OSError, AttributeError, TypeError):
                # Porta foi fechada ou desconectada fisicamente
                break
            time.sleep(0.1)

    def _inserir_dados_brutos_terminal(self, dados: str) -> None:
        """
        Insere texto bruto diretamente no Textbox do terminal na GUI.
        Processa caracteres de backspace (\x08) para apagar caracteres,
        mantendo a fidelidade da saída original do Cisco IOS estilo PuTTY.
        """
        self._terminal.configure(state="normal")
        if "\x08" in dados:
            for ch in dados:
                if ch == "\x08":
                    try:
                        self._terminal.delete("end-2c", "end-1c")
                    except Exception:
                        pass
                else:
                    self._terminal.insert("end", ch, "recebido")
        else:
            self._terminal.insert("end", dados, "recebido")

        self._terminal.configure(state="disabled")
        self._terminal.see("end")
        self._log_linhas.append(dados)

    def _obter_buffer_recente(self, n_caracteres: int = 300) -> str:
        """Retorna os últimos caracteres recebidos do switch no terminal."""
        if not self._log_linhas:
            return ""
        texto = "".join(self._log_linhas[-15:])
        return texto[-n_caracteres:]

    def _atualizar_status_ui(self, conectado: bool) -> None:
        """Sincroniza a UI com o estado atual da conexão."""
        if conectado:
            self._lbl_status.configure(text="● Conectado", text_color=COR_VERDE)
            self._btn_conectar.configure(state="disabled")
            self._btn_desconectar.configure(state="normal")
        else:
            self._lbl_status.configure(text="● Desconectado", text_color=COR_VERMELHO)
            self._btn_conectar.configure(state="normal")
            self._btn_desconectar.configure(state="disabled")

    def executar_auto_login_serial(self):
        usuario = self.entry_serial_user.get().strip() if self.entry_serial_user else ""
        senha = self.entry_serial_pass.get() if self.entry_serial_pass else ""

        if not usuario or not senha:
            self.after(0, self._log_terminal, "Aviso: Preencha Username e Password.", "aviso")
            return

        if not self._serial_client or not self._serial_client.conectado:
            self.after(0, self._log_terminal, "Erro: Porta serial não está aberta.", "erro")
            return

        def login_rotina():
            try:
                self.after(0, self._log_terminal, "🔑 Iniciando injeção direta de Auto-Login...", "info")
                if self.btn_auto_login:
                    self.after(0, self.btn_auto_login.configure, {"state": "disabled", "text": "⏳ Logando..."})

                # Acede ao objeto serial físico diretamente
                porta_com = getattr(self._serial_client, "_serial", None) or self.serial_conn

                if not porta_com or not porta_com.is_open:
                    self.after(0, self._log_terminal, "Erro: Porta serial não está aberta.", "erro")
                    return

                # 1. Acorda o terminal
                porta_com.write(b"\r")
                porta_com.flush()
                time.sleep(0.8)

                # Verifica se o switch já está em modo privilegiado (#)
                buffer_inicial = self._obter_buffer_recente().rstrip()
                if buffer_inicial.endswith("#"):
                    self.after(0, self._log_terminal, "ℹ️ Console já está autenticado em modo privilegiado (#).", "sucesso")
                    return

                # 2. Injeta Username e UM único Carriage Return (\r)
                porta_com.write(usuario.encode("ascii", errors="replace") + b"\r")
                porta_com.flush()
                time.sleep(0.8)

                # 3. Injeta Password no prompt protegido do Cisco
                porta_com.write(senha.encode("ascii", errors="replace") + b"\r")
                porta_com.flush()
                time.sleep(1.0)

                # 4. Verifica o prompt pós-login
                buffer_pos = self._obter_buffer_recente().rstrip()

                # Usuários com 'privilege 15' (como admin) já entram
                # diretamente em modo privilegiado (prompt terminado em '#').
                # NUNCA envie a senha quando o prompt já for '#', pois ela seria digitada como comando na tela!
                if buffer_pos.endswith(">"):
                    # Usuário comum (não-privilegiado), precisa de enable
                    porta_com.write(b"enable\r")
                    porta_com.flush()
                    time.sleep(0.8)
                    # Apenas se o switch realmente solicitar a senha de enable
                    if "password" in self._obter_buffer_recente().lower():
                        porta_com.write(senha.encode("ascii", errors="replace") + b"\r")
                        porta_com.flush()
                        time.sleep(0.5)

                self.after(0, self._log_terminal, "✅ Auto-Login concluído com sucesso.", "sucesso")

            except Exception as e:
                self.after(0, self._log_terminal, f"Erro no auto-login: {e}", "erro")
            finally:
                if self.btn_auto_login:
                    self.after(0, self.btn_auto_login.configure, {"state": "normal", "text": "🔑 Auto-Login"})

        threading.Thread(target=login_rotina, daemon=True).start()

    def executar_logout_serial(self) -> None:
        """Envia sequência de logout para o switch via console sem fechar a porta COM."""
        if not self._serial_client or not self._serial_client.conectado:
            self._log_terminal("AVISO: Conecte-se a uma porta serial antes de fazer logout.", "erro")
            return

        def logout_rotina():
            try:
                porta_com = getattr(self._serial_client, "_serial", None) or self.serial_conn
                if not porta_com or not porta_com.is_open:
                    return

                self.after(0, self._log_terminal, "🔒 Encerrando sessão do console (logout)...", "info")
                with self._serial_client._lock:
                    porta_com.write(b"\x03\rend\rexit\r")
                    porta_com.flush()
                time.sleep(0.5)
                # Envia um Enter limpo para forçar o switch a exibir o prompt de Username:
                with self._serial_client._lock:
                    porta_com.write(b"\r")
                    porta_com.flush()
                self.after(0, self._log_terminal, "✅ Sessão deslogada com sucesso. Switch pronto para novo login.", "sucesso")
            except Exception as e:
                self.after(0, self._log_terminal, f"Erro ao deslogar: {e}", "erro")

        threading.Thread(target=logout_rotina, daemon=True).start()

    # ------------------------------------------------------------------
    # VLANs adicionais
    # ------------------------------------------------------------------

    def _adicionar_vlan(self) -> None:
        """Valida e adiciona uma VLAN à lista interna."""
        vid_str  = self._var_vlan_id.get().strip()
        vnome    = self._var_vlan_nome.get().strip()

        if not vid_str or not vnome:
            messagebox.showwarning("Atenção", "Preencha ID e Nome da VLAN.")
            return

        try:
            vid = int(vid_str)
            if not (1 <= vid <= 4094):
                raise ValueError()
        except ValueError:
            messagebox.showerror("Erro", "ID de VLAN inválido (1–4094).")
            return

        # Verifica duplicidade
        if any(v.vlan_id == vid for v in self._vlans_adicionais):
            messagebox.showwarning("Atenção", f"VLAN {vid} já foi adicionada.")
            return

        self._vlans_adicionais.append(VlanConfig(vlan_id=vid, nome=vnome))
        self._var_vlan_id.set("")
        self._var_vlan_nome.set("")
        self._atualizar_lista_vlans()
        self._log_terminal(f"VLAN {vid} ({vnome}) adicionada à lista.", "sucesso")

    def _remover_vlan(self, vlan: VlanConfig) -> None:
        """Remove uma VLAN da lista e atualiza o painel."""
        self._vlans_adicionais = [v for v in self._vlans_adicionais if v.vlan_id != vlan.vlan_id]
        self._atualizar_lista_vlans()

    def _atualizar_lista_vlans(self) -> None:
        """Redesenha a lista visual de VLANs adicionadas."""
        for widget in self._frame_lista_vlans.winfo_children():
            widget.destroy()

        if not self._vlans_adicionais:
            ctk.CTkLabel(
                self._frame_lista_vlans,
                text="Nenhuma VLAN adicional configurada.",
                font=("Segoe UI", 9),
                text_color=COR_CINZA,
            ).pack(anchor="w", padx=6, pady=4)
            return

        for vlan in self._vlans_adicionais:
            row = ctk.CTkFrame(self._frame_lista_vlans, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(
                row,
                text=f"VLAN {vlan.vlan_id:>4}  –  {vlan.nome}",
                font=("Consolas", 10),
                text_color=COR_DESTAQUE,
            ).pack(side="left", padx=4)
            ctk.CTkButton(
                row,
                text="✕",
                width=22,
                height=20,
                fg_color="#6b1a1a",
                hover_color="#8a2424",
                font=("Segoe UI", 9),
                command=lambda v=vlan: self._remover_vlan(v),
            ).pack(side="right", padx=4)

    # ------------------------------------------------------------------
    # Provisionamento Day-0 (Template Fixo Padrão)
    # ------------------------------------------------------------------

    def iniciar_provisionamento(self) -> None:
        """
        Ponto de entrada do botão '🚀 Aplicar Configuração Padrão'.

        1. Valida se a porta serial está conectada.
        2. Valida obrigatoriamente se Hostname, IP, Máscara e Gateway estão preenchidos.
        3. Se tudo estiver OK, lança a automação em Thread separada para não travar a GUI.
        """
        # --- 1. Validação da conexão serial ---
        if not self._serial_client or not self._serial_client.conectado:
            self._log_terminal(
                "ERRO: Nenhuma porta serial conectada. Clique em 'Conectar' primeiro.",
                "erro",
            )
            return

        # --- 2. Verificação de thread em execução ---
        if self._thread_envio and self._thread_envio.is_alive():
            self._log_terminal(
                "AVISO: Já existe uma operação em andamento na porta serial. Aguarde.",
                "erro",
            )
            return

        # --- 3. Coleta e validação rigorosa dos campos obrigatórios ---
        hostname = self._var_hostname.get().strip().replace(" ", "-")
        self._var_hostname.set(hostname)
        ip_mgmt  = self._var_ip_mgmt.get().strip()
        mascara  = self._var_mascara.get().strip()
        gateway  = self._var_gateway.get().strip()

        campos_obrigatorios = [
            (hostname, "Hostname"),
            (ip_mgmt,  "IP de Gerência"),
            (mascara,  "Máscara de Sub-rede"),
            (gateway,  "Default Gateway"),
        ]
        for valor, nome in campos_obrigatorios:
            if not valor:
                self._log_terminal(
                    f"ERRO: Campo obrigatório não preenchido → '{nome}'.",
                    "erro",
                )
                return

        try:
            vlan_id = int(self._var_vlan_mgmt.get().strip() or "1")
        except ValueError:
            self._log_terminal("ERRO: ID da VLAN de gerência inválido.", "erro")
            return

        # --- 4. Feedback visual e disparo da Thread ---
        self.btn_aplicar_padrao.configure(
            state="disabled", text="⏳  Provisionando…"
        )
        self.btn_resetar.configure(state="disabled")
        self._log_terminal("=" * 56, "titulo")
        self._log_terminal("  INICIANDO CONFIGURAÇÃO PADRÃO (SERIAL)", "titulo")
        self._log_terminal(f"  Host    : {hostname}", "info")
        self._log_terminal(f"  IP/Mask : {ip_mgmt} / {mascara}", "info")
        self._log_terminal(f"  Gateway : {gateway}", "info")
        self._log_terminal(f"  VLAN mgmt: {vlan_id}", "info")
        self._log_terminal("=" * 56, "titulo")

        self._thread_envio = threading.Thread(
            target=self.executar_comandos_cisco,
            args=(hostname, ip_mgmt, mascara, gateway, vlan_id),
            daemon=True,
        )
        self._thread_envio.start()

    def executar_comandos_cisco(
        self,
        hostname: str,
        ip_mgmt: str,
        mascara: str,
        gateway: str,
        vlan_id: int,
    ) -> None:
        """
        Executado em thread separada (daemon).
        Realiza o provisionamento Day-0 completo no switch Cisco:

          1. Verifica a segurança inicial do buffer:
             - Aborta imediatamente se o switch exigir Username/Password.
             - Responde 'no' se o assistente de configuração inicial estiver ativo.
          2. Obtém a lista oficial de comandos via CiscoTemplates (Princípio DRY).
          3. Envia os comandos sequencialmente, aplicando pausa ativa com detecção
             de [OK] para a geração da chave RSA.
          4. Grava na NVRAM (write memory) e finaliza de forma thread-safe.
        """
        serial_obj = self._serial_client

        # ETAPA 0 — Verificação de Segurança e Abort (Prevenção de Comandos Cegos)
        try:
            serial_obj.verificar_seguranca_inicial()
        except SwitchAcessoNegadoException as exc:
            self.after(0, self._log_terminal, str(exc), "erro")
            self.after(0, self._on_provisionamento_concluido, False)
            return
        except Exception as exc:
            self.after(0, self._log_terminal, f"ERRO na verificação de segurança: {exc}", "erro")
            self.after(0, self._on_provisionamento_concluido, False)
            return

        # ETAPA 1 — Obter lista oficial de comandos via CiscoTemplates (DRY)
        hostname = hostname.strip().replace(" ", "-")
        comandos = CiscoTemplates.get_standard_day0_config(
            hostname=hostname,
            vlan_id=vlan_id,
            ip=ip_mgmt,
            mascara=mascara,
            gateway=gateway,
        )

        # ETAPA 2 — Envio sequencial com leitura de buffer e pausa interativa
        total = len(comandos)
        porta_com = self.serial_conn
        if not porta_com or not porta_com.is_open:
            self.after(0, self._log_terminal, "ERRO: Porta serial não está aberta para envio.", "erro")
            self.after(0, self._on_provisionamento_concluido, False)
            return

        self._provisionamento_ativo = True
        self._buffer_provisionamento = ""
        self.esperando_resposta = False

        try:
            in_banner = False
            for idx, cmd in enumerate(comandos, start=1):
                # Atualização do progresso visual
                pct = int(idx / total * 100)
                self.after(
                    0,
                    self.btn_aplicar_padrao.configure,
                    {"text": f"⏳  Enviando… {pct}% ({idx}/{total})"},
                )

                cmd_limpo = cmd.strip()
                eh_rsa = "crypto key generate rsa" in cmd.lower()
                eh_inicio_banner = "banner motd %" in cmd.lower()
                eh_write_mem = "write memory" in cmd.lower()

                # Se for a linha isolada com o delimitador %, fim do banner
                if cmd_limpo == "%":
                    in_banner = False

                # Limpa buffer antes do envio do novo comando
                self._buffer_provisionamento = ""

                # Envia comando via porta serial com \r puro
                with self._serial_client._lock:
                    porta_com.write(cmd.encode("ascii", errors="replace") + b"\r")
                    porta_com.flush()

                # Pausa para processamento do switch
                if eh_inicio_banner or in_banner:
                    in_banner = True
                    time.sleep(0.3)
                elif eh_write_mem:
                    time.sleep(3.0)
                elif eh_rsa:
                    time.sleep(2.0)
                else:
                    time.sleep(1.0)

                # Lê o que o switch respondeu diretamente na porta, se houver
                try:
                    if porta_com.in_waiting > 0:
                        saida = porta_com.read(porta_com.in_waiting).decode("ascii", errors="ignore")
                        if saida:
                            self.after(0, self._inserir_dados_brutos_terminal, saida)
                            self._buffer_provisionamento += saida
                except Exception:
                    pass

                # Verifica se o switch está a fazer uma pergunta ([yes/no] ou [confirm])
                texto_verificacao = (self._buffer_provisionamento + " " + self._obter_buffer_recente(400)).lower()
                if "[yes/no]" in texto_verificacao or "[confirm]" in texto_verificacao:
                    self.after(
                        0,
                        self._log_terminal,
                        "\n⏸️ SCRIPT PAUSADO: O switch requer confirmação. Digite 'yes' ou 'no' no terminal abaixo e pressione Enter para continuar.\n",
                        "aviso",
                    )
                    self.esperando_resposta = True
                    # Pausa a automação até o utilizador responder manualmente no terminal
                    while self.esperando_resposta:
                        if not self.serial_conn or not self.serial_conn.is_open:
                            break
                        time.sleep(0.5)

                    time.sleep(2.0)
                    self._buffer_provisionamento = ""
                elif eh_rsa:
                    # Se não pediu confirmação [yes/no], aguarda a chave RSA ser gerada
                    self._log_terminal("Aguardando finalização do processo de chave RSA...", "info")
                    time.sleep(4.0)

            self._provisionamento_ativo = False
            self.after(0, self._on_provisionamento_concluido, True)

        except Exception as exc:
            self._provisionamento_ativo = False
            self.esperando_resposta = False
            self.after(0, self._log_terminal, f"ERRO FATAL DURANTE ENVIO: {exc}", "erro")
            self.after(0, self._on_provisionamento_concluido, False)

    def _on_provisionamento_concluido(self, sucesso: bool) -> None:
        """
        Executado na main thread (via after()) ao término do provisionamento.
        Reativa os botões e exibe o resultado final no terminal.
        """
        self._provisionamento_ativo = False
        self.esperando_resposta = False
        self.btn_aplicar_padrao.configure(
            state="normal", text="🚀  Aplicar Configuração Padrão"
        )
        self.btn_resetar.configure(state="normal")
        if sucesso:
            self._log_terminal("=" * 56, "titulo")
            self._log_terminal("  ✅  PROVISIONAMENTO CONCLUÍDO COM SUCESSO!", "sucesso")
            self._log_terminal("  Configuração salva na NVRAM (write memory).", "sucesso")
            self._log_terminal("=" * 56, "titulo")
        else:
            self._log_terminal("=" * 56, "titulo")
            self._log_terminal("  ❌  PROVISIONAMENTO INTERROMPIDO POR ERRO.", "erro")
            self._log_terminal("  Verifique o log acima para diagnóstico.", "erro")
            self._log_terminal("=" * 56, "titulo")

    # ------------------------------------------------------------------
    # Reset de Fábrica (Factory Reset / Wipe Cisco IOS)
    # ------------------------------------------------------------------

    def iniciar_reset_switch(self) -> None:
        """
        Ponto de entrada do botão '⚠️ Resetar Switch (Padrão de Fábrica)'.

        1. Valida a conexão serial.
        2. Solicita confirmação explícita do analista via modal de alerta.
        3. Se confirmado, dispara a thread para executar a sequência de
           write erase, remoção de vlan.dat e reload.
        """
        if not self._serial_client or not self._serial_client.conectado or not self.serial_conn:
            self._log_terminal(
                "ERRO: Nenhuma porta serial conectada. Conecte-se antes de resetar o switch.",
                "erro",
            )
            return

        if self._thread_envio and self._thread_envio.is_alive():
            self._log_terminal(
                "AVISO: Já existe uma operação em andamento na porta serial. Aguarde.",
                "erro",
            )
            return

        confirmacao = messagebox.askyesno(
            "⚠️ Confirmar Reset de Fábrica",
            "ATENÇÃO: Esta ação é DESTRUTIVA!\n\n"
            "Todos os arquivos de configuração (startup-config) e as VLANs salvas (vlan.dat) "
            "serão permanentemente apagados e o switch será reiniciado.\n\n"
            "Tem certeza de que deseja restaurar as configurações de fábrica deste switch?",
            icon="warning",
        )
        if not confirmacao:
            self._log_terminal("Operação de Reset de Fábrica cancelada pelo usuário.", "info")
            return

        # Desabilita botões para evitar concorrência
        self.btn_resetar.configure(state="disabled", text="⏳  Resetando Switch…")
        self.btn_aplicar_padrao.configure(state="disabled")

        senha_informada = self._var_secret.get().strip()

        self._thread_envio = threading.Thread(
            target=self.executar_reset_cisco,
            args=(senha_informada,),
            daemon=True,
        )
        self._thread_envio.start()

    def executar_reset_cisco(self, senha_enable: str) -> None:
        """
        Executado em thread secundária para resetar o switch Cisco:
          1. Acorda a sessão serial.
          2. Entra em modo privilegiado (enable) — enviando senha caso solicitada.
          3. Executa 'write erase' e confirma.
          4. Executa 'delete flash:vlan.dat' e confirma.
          5. Executa 'reload', rejeita salvar modificações ('no') e confirma reboot.
        """
        conn = self.serial_conn

        self.after(0, self._log_terminal, "=" * 56, "titulo")
        self.after(0, self._log_terminal, "  INICIANDO FACTORY RESET DO SWITCH CISCO", "titulo")
        self.after(0, self._log_terminal, "  1. Acordando sessão e entrando em enable...", "info")
        self.after(0, self._log_terminal, "=" * 56, "titulo")

        try:
            # 1. Acorda a sessão
            conn.write(b"\r\n\r\n")
            conn.flush()
            time.sleep(1.0)

            # 2. Modo Privilegiado
            conn.write(b"enable\r\n")
            conn.flush()
            time.sleep(1.0)

            # Se o switch tiver senha de enable configurada e foi digitada na interface
            if senha_enable:
                conn.write(f"{senha_enable}\r\n".encode("utf-8"))
                conn.flush()
                time.sleep(1.0)

            # 3. Write erase (limpeza de NVRAM)
            self.after(0, self._log_terminal, ">> write erase", "enviado")
            conn.write(b"write erase\r\n")
            conn.flush()
            time.sleep(1.5)

            # Confirma o [confirm] da NVRAM
            conn.write(b"\r\n")
            conn.flush()
            time.sleep(2.5)

            # 4. Apagar vlan.dat (remoção de VLANs da memória flash)
            self.after(0, self._log_terminal, ">> delete flash:vlan.dat", "enviado")
            conn.write(b"delete flash:vlan.dat\r\n")
            conn.flush()
            time.sleep(1.5)

            # Confirma nome do arquivo [vlan.dat]
            conn.write(b"\r\n")
            conn.flush()
            time.sleep(1.0)

            # Confirma exclusão [confirm]
            conn.write(b"\r\n")
            conn.flush()
            time.sleep(2.0)

            # 5. Reload do switch
            self.after(0, self._log_terminal, ">> reload", "enviado")
            conn.write(b"reload\r\n")
            conn.flush()
            time.sleep(2.0)

            # Se o IOS perguntar "System configuration has been modified. Save? [yes/no]:"
            # DEVE-SE responder 'no' para não ressuscitar a configuração recém-apagada!
            conn.write(b"no\r\n")
            conn.flush()
            time.sleep(1.5)

            # Confirma o "Proceed with reload? [confirm]"
            conn.write(b"\r\n")
            conn.flush()
            time.sleep(1.5)

            self.after(0, self._on_reset_concluido, True)

        except Exception as exc:
            self.after(0, self._log_terminal, f"ERRO no processo de Reset: {exc}", "erro")
            self.after(0, self._on_reset_concluido, False)

    def _on_reset_concluido(self, sucesso: bool) -> None:
        """
        Executado na main thread (via after()) ao término do reset.
        Reativa os botões e exibe aviso de reboot no terminal.
        """
        self.btn_resetar.configure(state="normal", text="⚠️  Resetar Switch (Padrão de Fábrica)")
        self.btn_aplicar_padrao.configure(state="normal")
        if sucesso:
            self._log_terminal("=" * 56, "titulo")
            self._log_terminal("  ✅  RESET DE FÁBRICA EXECUTADO COM SUCESSO!", "sucesso")
            self._log_terminal("  Configurações apagadas e comando 'reload' enviado.", "sucesso")
            self._log_terminal("  Aguarde o switch reiniciar (acompanhe o boot no terminal).", "info")
            self._log_terminal("=" * 56, "titulo")
            messagebox.showinfo(
                "Reset Concluído",
                "O switch foi resetado para os padrões de fábrica e está reiniciando!\n\n"
                "Você pode acompanhar o processo de boot pelo terminal à direita."
            )
        else:
            self._log_terminal("=" * 56, "titulo")
            self._log_terminal("  ❌  FALHA DURANTE O RESET DO SWITCH.", "erro")
            self._log_terminal("  Verifique a conexão serial ou se o switch está no prompt correto.", "erro")
            self._log_terminal("=" * 56, "titulo")

    # ------------------------------------------------------------------
    # Terminal / Log & Console Interativo (Emulação PuTTY)
    # ------------------------------------------------------------------

    def _on_terminal_keypress(self, event) -> str:
        """Captura a tecla pressionada no textbox e envia direto via Serial (estilo PuTTY)."""
        if not self._serial_client or not getattr(self._serial_client, "_serial", None):
            return "break"  # Ignora se não houver conexão serial ativa

        porta_com = self._serial_client._serial
        if not porta_com or not porta_com.is_open:
            return "break"

        try:
            with self._serial_client._lock:
                # Tratamento de teclas especiais
                if event.keysym == "Return":
                    porta_com.write(b"\r")
                    porta_com.flush()
                    # Se o script de automação estiver pausado à espera de resposta:
                    if getattr(self, "esperando_resposta", False):
                        self.esperando_resposta = False
                        self.after(0, self._log_terminal, "▶️ Resposta enviada. A retomar a automação...\n", "info")
                elif event.keysym == "space":
                    # Espaço imediato (ideal para prompts interativos como --More--)
                    porta_com.write(b" ")
                    porta_com.flush()
                elif event.keysym == "BackSpace":
                    # Envia o byte de backspace (\x08)
                    porta_com.write(b"\x08")
                    porta_com.flush()
                elif event.keysym.lower() == "c" and (event.state & 0x4):
                    # Ctrl+C (\x03)
                    porta_com.write(b"\x03")
                    porta_com.flush()
                elif event.keysym == "Tab":
                    # Tab completion do Cisco IOS
                    porta_com.write(b"\t")
                    porta_com.flush()
                elif event.keysym == "Up":
                    # Seta para cima (histórico do Cisco IOS)
                    porta_com.write(b"\x1b[A")
                    porta_com.flush()
                elif event.keysym == "Down":
                    # Seta para baixo
                    porta_com.write(b"\x1b[B")
                    porta_com.flush()
                elif event.keysym == "Right":
                    porta_com.write(b"\x1b[C")
                    porta_com.flush()
                elif event.keysym == "Left":
                    porta_com.write(b"\x1b[D")
                    porta_com.flush()
                elif len(event.char) == 1 and event.char.isprintable():
                    # Envia letras, números e símbolos
                    porta_com.write(event.char.encode("ascii", errors="ignore"))
                    porta_com.flush()
        except Exception as e:
            logger.error("Erro ao enviar tecla para o switch: %s", e)

        # Retorna "break" para impedir que o Tkinter escreva a tecla localmente.
        # O texto só aparecerá na tela porque a thread de leitura serial vai 
        # capturar o eco do Cisco e imprimir no textbox!
        return "break"

    def _on_terminal_paste(self, event=None) -> str:
        """Cola texto da área de transferência enviando direto para a porta serial."""
        if not self._serial_client or not getattr(self._serial_client, "_serial", None):
            return "break"
        porta_com = self._serial_client._serial
        if not porta_com or not porta_com.is_open:
            return "break"
        try:
            texto = self.clipboard_get()
            if texto:
                texto_cisco = texto.replace("\r\n", "\r").replace("\n", "\r")
                with self._serial_client._lock:
                    porta_com.write(texto_cisco.encode("ascii", errors="ignore"))
                    porta_com.flush()
        except Exception as exc:
            logger.error("Erro ao colar texto no terminal serial: %s", exc)
        return "break"

    def _enviar_comando_manual(self, event=None) -> None:
        """Envia comando para a porta serial e destrava se pausado."""
        if not self._serial_client or not self._serial_client.conectado or not self.serial_conn:
            self._log_terminal("AVISO: Conecte-se a uma porta serial antes de enviar comandos.", "erro")
            return

        cmd = self._entry_comando.get().strip() if self._entry_comando else ""
        if self._entry_comando:
            self._entry_comando.delete(0, "end")

        if cmd:
            self._historico_comandos.append(cmd)
            self._idx_historico = len(self._historico_comandos)

        try:
            with self._serial_client._lock:
                self.serial_conn.write(f"{cmd}\r".encode("ascii", errors="replace"))
                self.serial_conn.flush()
        except Exception as exc:
            self._log_terminal(f"Erro ao enviar comando: {exc}", "erro")

        # Se o script estava pausado à espera desta resposta, liberta a continuação
        if getattr(self, "esperando_resposta", False):
            self.esperando_resposta = False
            self.after(0, self._log_terminal, "▶️ Resposta enviada. A retomar a automação...\n", "info")

    # Alias para compatibilidade
    enviar_comando_manual_terminal = _enviar_comando_manual

    def _enviar_caractere_especial(self, char_seq: str) -> None:
        """Envia caracteres/sinais de controle como Enter (\\r\\n) ou Ctrl+C (\\x03)."""
        if not self._serial_client or not self._serial_client.conectado or not self.serial_conn:
            self._log_terminal("AVISO: Conecte-se a uma porta serial antes de enviar comandos.", "erro")
            return
        try:
            with self._serial_client._lock:
                self.serial_conn.write(char_seq.encode("utf-8"))
                self.serial_conn.flush()
        except Exception as exc:
            self._log_terminal(f"Erro ao enviar sinal: {exc}", "erro")

    def _historico_anterior(self, event=None) -> str:
        """Navega para o comando anterior no histórico (tecla Cima)."""
        if not self._entry_comando or not self._historico_comandos:
            return "break"
        if self._idx_historico > 0:
            self._idx_historico -= 1
        elif self._idx_historico == -1:
            self._idx_historico = len(self._historico_comandos) - 1

        cmd = self._historico_comandos[self._idx_historico]
        self._entry_comando.delete(0, "end")
        self._entry_comando.insert(0, cmd)
        return "break"

    def _historico_proximo(self, event=None) -> str:
        """Navega para o próximo comando no histórico (tecla Baixo)."""
        if not self._entry_comando or not self._historico_comandos:
            return "break"
        if 0 <= self._idx_historico < len(self._historico_comandos) - 1:
            self._idx_historico += 1
            cmd = self._historico_comandos[self._idx_historico]
            self._entry_comando.delete(0, "end")
            self._entry_comando.insert(0, cmd)
        else:
            self._idx_historico = len(self._historico_comandos)
            self._entry_comando.delete(0, "end")
        return "break"

    def _callback_log_serial(self, mensagem: str, nivel: str) -> None:
        """
        Callback registrado na instância CiscoSerial.
        Redireciona mensagens para o terminal gráfico usando after() (thread-safe).
        """
        self.after(0, self._log_terminal, mensagem, nivel)

    def _log_terminal(self, mensagem: str, nivel: str = "info") -> None:
        """Insere uma linha no terminal com timestamp e coloração por nível."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._terminal.configure(state="normal")

        # Timestamp cinza
        self._terminal.insert("end", f"[{timestamp}] ", "tempo")

        # Mensagem colorida conforme nível
        self._terminal.insert("end", f"{mensagem}\n", nivel)

        # Cache para exportação
        self._log_linhas.append(f"[{timestamp}] {mensagem}")

        self._terminal.configure(state="disabled")
        self._terminal.see("end")  # auto-scroll

    def _limpar_log(self) -> None:
        """Limpa o conteúdo do terminal e o cache interno."""
        self._terminal.configure(state="normal")
        self._terminal.delete("1.0", "end")
        self._terminal.configure(state="disabled")
        self._log_linhas.clear()
        self._log_terminal("Log limpo.", "info")

    def _exportar_log(self) -> None:
        """Salva o conteúdo completo do terminal em um arquivo .txt para evidências e auditoria."""
        conteudo_terminal = self._terminal.get("1.0", "end-1c").strip()
        if not conteudo_terminal and not self._log_linhas:
            messagebox.showinfo("Exportar", "Nenhum conteúdo de log no terminal para exportar.")
            return

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        timestamp_arquivo = datetime.now().strftime("%Y%m%d_%H%M%S")

        caminho = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Arquivo de Texto", "*.txt"), ("Todos os arquivos", "*.*")],
            initialfile=f"auditoria_switch_{timestamp_arquivo}.txt",
            title="Exportar Log para Auditoria",
        )
        if not caminho:
            return

        cabecalho = (
            "=" * 80 + "\n"
            "Sessão exportada do PROVISIONADOR DE SWITCH - Operador/Auditoria\n"
            f"Data/Hora: {timestamp_str}\n"
            + "=" * 80 + "\n\n"
        )

        try:
            texto_exportar = conteudo_terminal if conteudo_terminal else "\n".join(self._log_linhas)
            with open(caminho, "w", encoding="utf-8") as f:
                f.write(cabecalho + texto_exportar + "\n")
            messagebox.showinfo("Exportação Concluída", f"Log de auditoria exportado com sucesso em:\n{caminho}")
            self._log_terminal(f"Log de auditoria exportado: {os.path.basename(caminho)}", "sucesso")
        except OSError as exc:
            messagebox.showerror("Erro ao salvar", str(exc))

    # ------------------------------------------------------------------
    # ABA 2: Dashboard SSH (Day-1+ Port Management)
    # ------------------------------------------------------------------

    def _construir_dashboard_ssh(self, pai: ctk.CTkFrame) -> None:
        """Constrói a interface visual completa do Dashboard SSH e COM."""
        # Contêiner principal com padding
        corpo = ctk.CTkFrame(pai, fg_color="transparent")
        corpo.pack(fill="both", expand=True, padx=4, pady=4)

        # --------------------------------------------------------------
        # 1. Parte Superior (Autenticação e Conexão: SSH ou Console COM)
        # --------------------------------------------------------------
        frame_auth = ctk.CTkFrame(
            corpo,
            fg_color=COR_FRAME,
            corner_radius=10,
            border_width=1,
            border_color=COR_FRAME_BORDA,
        )
        frame_auth.pack(fill="x", padx=0, pady=(0, 8))

        ctk.CTkLabel(
            frame_auth,
            text="🌐  Conexão do Switch & Leitura de Portas (SSH / Console COM)",
            font=FONTE_TITULO,
            text_color=COR_DESTAQUE,
        ).pack(anchor="w", padx=16, pady=(10, 4))

        ctk.CTkFrame(frame_auth, height=1, fg_color=COR_FRAME_BORDA).pack(
            fill="x", padx=16, pady=(0, 8)
        )

        frame_auth_campos = ctk.CTkFrame(frame_auth, fg_color="transparent")
        frame_auth_campos.pack(fill="x", padx=16, pady=(0, 10))

        # Seletor de Modo: SSH vs Console COM
        ctk.CTkLabel(
            frame_auth_campos, text="Método:", font=FONTE_LABEL_BOLD, text_color=COR_DESTAQUE
        ).pack(side="left", padx=(0, 6))

        self.seg_modo_conexao = ctk.CTkSegmentedButton(
            frame_auth_campos,
            values=["SSH (Rede IP)", "Console (Porta COM)"],
            variable=self._var_modo_conexao,
            command=self._alternar_modo_conexao_dashboard,
            font=FONTE_LABEL_BOLD,
            selected_color=COR_BOTAO_PRIMARIO,
            selected_hover_color=COR_BOTAO_HOVER,
            width=240,
            height=30,
        )
        self.seg_modo_conexao.pack(side="left", padx=(0, 12))

        # Container dos campos SSH
        self.frame_campos_ssh = ctk.CTkFrame(frame_auth_campos, fg_color="transparent")

        ctk.CTkLabel(self.frame_campos_ssh, text="IP:", font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(
            self.frame_campos_ssh,
            textvariable=self._var_ssh_ip,
            placeholder_text="ex: 192.0.2.10",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            width=130,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(self.frame_campos_ssh, text="User:", font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(
            self.frame_campos_ssh,
            textvariable=self._var_ssh_user,
            placeholder_text="admin",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            width=110,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(self.frame_campos_ssh, text="Pass:", font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(side="left", padx=(0, 4))
        self.entry_ssh_password = ctk.CTkEntry(
            self.frame_campos_ssh,
            textvariable=self._var_ssh_pass,
            placeholder_text="••••••",
            show="*",
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            width=110,
        )
        self.entry_ssh_password.pack(side="left", padx=(0, 10))
        self.entry_ssh_password.bind("<Return>", lambda event: self._conectar_e_ler_portas_dashboard())

        # Container dos campos Console COM
        self.frame_campos_com = ctk.CTkFrame(frame_auth_campos, fg_color="transparent")

        ctk.CTkLabel(self.frame_campos_com, text="Porta:", font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(side="left", padx=(0, 4))
        self.combo_dash_porta = ctk.CTkComboBox(
            self.frame_campos_com,
            variable=self._var_dash_com_porta,
            values=["COM1"],
            width=105,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            button_color=COR_BOTAO_PRIMARIO,
            font=FONTE_LABEL,
            dropdown_fg_color="#0d1117",
        )
        self.combo_dash_porta.pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            self.frame_campos_com,
            text="🔄",
            width=30,
            height=28,
            command=self._atualizar_portas_dashboard,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(self.frame_campos_com, text="Baud:", font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(side="left", padx=(0, 4))
        self.combo_dash_baud = ctk.CTkComboBox(
            self.frame_campos_com,
            variable=self._var_dash_com_baud,
            values=["9600", "19200", "38400", "57600", "115200"],
            width=95,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            button_color=COR_BOTAO_PRIMARIO,
            font=FONTE_LABEL,
            dropdown_fg_color="#0d1117",
        )
        self.combo_dash_baud.pack(side="left", padx=(0, 10))

        # Botão Conectar e Ler Portas
        self.btn_ssh_conectar = ctk.CTkButton(
            frame_auth_campos,
            text="🔌  Conectar e Ler Portas",
            command=self._conectar_e_ler_portas_dashboard,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
            font=FONTE_LABEL_BOLD,
            width=175,
            height=32,
        )
        self.btn_ssh_conectar.pack(side="left", padx=(0, 8))

        # Botão Desconectar
        self.btn_ssh_desconectar = ctk.CTkButton(
            frame_auth_campos,
            text="Desconectar",
            command=self._desconectar_dashboard,
            state="disabled",
            fg_color="#6b1a1a",
            hover_color="#8a2424",
            font=FONTE_LABEL_BOLD,
            width=95,
            height=32,
        )
        self.btn_ssh_desconectar.pack(side="left", padx=(0, 10))

        # Status Visual
        self.lbl_ssh_status = ctk.CTkLabel(
            frame_auth_campos,
            text="● Desconectado",
            font=FONTE_LABEL_BOLD,
            text_color=COR_VERMELHO,
        )
        self.lbl_ssh_status.pack(side="left", padx=(4, 0))

        # Ativa inicialmente o modo selecionado
        self._alternar_modo_conexao_dashboard(self._var_modo_conexao.get())

        # --------------------------------------------------------------
        # 2. Parte Central (Dashboard Visual de Portas + Live Preview)
        # --------------------------------------------------------------
        frame_dashboard = ctk.CTkFrame(
            corpo,
            fg_color="transparent",
        )
        frame_dashboard.pack(fill="both", expand=True, padx=0, pady=(0, 8))
        frame_dashboard.columnconfigure(0, weight=7)  # 70% da largura
        frame_dashboard.columnconfigure(1, weight=3)  # 30% da largura
        frame_dashboard.rowconfigure(0, weight=1)

        # Painel Esquerdo (70%): Tabela de Portas
        painel_esquerdo_portas = ctk.CTkFrame(
            frame_dashboard,
            fg_color=COR_FRAME,
            corner_radius=10,
            border_width=1,
            border_color=COR_FRAME_BORDA,
        )
        painel_esquerdo_portas.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)

        # --- Painel de Configuração em Lote (Interface Range - Bulk) ---
        frame_bulk = ctk.CTkFrame(
            painel_esquerdo_portas,
            fg_color="#0e1726",
            corner_radius=8,
            border_width=1,
            border_color=COR_FRAME_BORDA,
        )
        frame_bulk.pack(fill="x", padx=10, pady=(10, 4))

        ctk.CTkLabel(
            frame_bulk,
            text="⚡ Lote:",
            font=FONTE_LABEL_BOLD,
            text_color=COR_DESTAQUE,
        ).pack(side="left", padx=(8, 4), pady=6)

        self.chk_sel_todas = ctk.CTkCheckBox(
            frame_bulk,
            text="Todas",
            variable=self._var_sel_todos,
            command=self._toggle_selecionar_todas_portas,
            font=FONTE_LABEL,
            width=65,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.chk_sel_todas.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(frame_bulk, text="Modo:", font=FONTE_LABEL, text_color=COR_CINZA).pack(side="left", padx=(0, 2))
        self.opt_bulk_modo = ctk.CTkOptionMenu(
            frame_bulk,
            values=["Access", "Trunk"],
            variable=self._var_bulk_modo,
            width=85,
            height=26,
            fg_color="#21262d",
            button_color="#30363d",
            font=FONTE_LABEL,
        )
        self.opt_bulk_modo.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(frame_bulk, text="VLAN:", font=FONTE_LABEL, text_color=COR_CINZA).pack(side="left", padx=(0, 2))
        self.entry_bulk_vlan = ctk.CTkEntry(
            frame_bulk,
            textvariable=self._var_bulk_vlan,
            placeholder_text="10",
            width=65,
            height=26,
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
        )
        self.entry_bulk_vlan.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(frame_bulk, text="Desc:", font=FONTE_LABEL, text_color=COR_CINZA).pack(side="left", padx=(0, 2))
        self.entry_bulk_desc = ctk.CTkEntry(
            frame_bulk,
            textvariable=self._var_bulk_desc,
            placeholder_text="ex: Setor Adm",
            width=115,
            height=26,
            font=FONTE_LABEL,
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
        )
        self.entry_bulk_desc.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(frame_bulk, text="Estado:", font=FONTE_LABEL, text_color=COR_CINZA).pack(side="left", padx=(0, 2))
        self.opt_bulk_estado = ctk.CTkOptionMenu(
            frame_bulk,
            values=["Ativa (no shutdown)", "Desativada (shutdown)"],
            variable=self._var_bulk_estado,
            width=140,
            height=26,
            fg_color="#21262d",
            button_color="#30363d",
            font=FONTE_LABEL,
        )
        self.opt_bulk_estado.pack(side="left", padx=(0, 6))

        ctk.CTkLabel(frame_bulk, text="PoE:", font=FONTE_LABEL, text_color=COR_CINZA).pack(side="left", padx=(0, 2))
        self.opt_bulk_poe = ctk.CTkOptionMenu(
            frame_bulk,
            values=["Auto (Ligado)", "Never (Desligado)"],
            variable=self._var_bulk_poe,
            width=130,
            height=26,
            fg_color="#21262d",
            button_color="#30363d",
            font=FONTE_LABEL,
        )
        self.opt_bulk_poe.pack(side="left", padx=(0, 8))

        self.btn_bulk_aplicar = ctk.CTkButton(
            frame_bulk,
            text="⚡ Aplicar em Lote",
            command=self._aplicar_configuracao_lote,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
            font=FONTE_LABEL_BOLD,
            width=120,
            height=26,
        )
        self.btn_bulk_aplicar.pack(side="left", padx=(0, 8))

        # Cabeçalho da Tabela
        cabec_tabela = ctk.CTkFrame(painel_esquerdo_portas, fg_color="#0d1117", height=32, corner_radius=6)
        cabec_tabela.pack(fill="x", padx=10, pady=(2, 4))
        cabec_tabela.pack_propagate(False)

        ctk.CTkLabel(cabec_tabela, text="Sel", width=30, font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(side="left", padx=(6, 2))
        ctk.CTkLabel(cabec_tabela, text="Porta", width=68, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="Status", width=68, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="MAC Address", width=125, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="Vizinhança (CDP)", width=125, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="Descrição da Interface", width=120, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="w").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="Modo", width=75, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="center").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="VLAN", width=75, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="center").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="Estado", width=130, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="center").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="PoE", width=120, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="center").pack(side="left", padx=2)
        ctk.CTkLabel(cabec_tabela, text="Speed", width=50, font=FONTE_LABEL_BOLD, text_color=COR_CINZA, anchor="w").pack(side="left", padx=(2, 4))

        # Área de Scroll com as Linhas das Portas
        self._frame_tabela_portas = ctk.CTkScrollableFrame(
            painel_esquerdo_portas,
            fg_color="#0d1117",
            corner_radius=6,
        )
        self._frame_tabela_portas.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Mensagem inicial enquanto não há portas carregadas
        self._lbl_tabela_vazia = ctk.CTkLabel(
            self._frame_tabela_portas,
            text="Nenhuma porta carregada.\nInsira as credenciais SSH acima e clique em 'Conectar e Ler Portas'.",
            font=("Segoe UI", 12),
            text_color=COR_CINZA,
            justify="center",
        )
        self._lbl_tabela_vazia.pack(expand=True, pady=60)

        # Painel Direito (30%): Console de Validação (Preview)
        painel_direito_preview = ctk.CTkFrame(
            frame_dashboard,
            fg_color=COR_FRAME,
            corner_radius=10,
            border_width=1,
            border_color=COR_FRAME_BORDA,
        )
        painel_direito_preview.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)

        ctk.CTkLabel(
            painel_direito_preview,
            text="💻  Console de Validação (Preview)",
            font=FONTE_TITULO,
            text_color=COR_DESTAQUE,
        ).pack(anchor="w", padx=14, pady=(10, 4))

        ctk.CTkFrame(painel_direito_preview, height=1, fg_color=COR_FRAME_BORDA).pack(
            fill="x", padx=14, pady=(0, 6)
        )

        ctk.CTkLabel(
            painel_direito_preview,
            text="Comandos IOS em tempo real para as portas alteradas:",
            font=("Segoe UI", 10),
            text_color=COR_CINZA,
        ).pack(anchor="w", padx=14, pady=(0, 6))

        self.textbox_preview_ssh = ctk.CTkTextbox(
            painel_direito_preview,
            font=("Consolas", 10),
            fg_color="#0d1117",
            text_color="#00ff88",
            border_color=COR_FRAME_BORDA,
            border_width=1,
            wrap="none",
            state="disabled",
        )
        self.textbox_preview_ssh.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        # Botão de Envio de Comando Manual
        self.btn_comando_manual = ctk.CTkButton(
            painel_direito_preview,
            text="📟  Enviar Comando Manual",
            command=self._abrir_modal_comando_manual,
            fg_color="#1f6feb",
            hover_color="#388bfd",
            font=FONTE_LABEL_BOLD,
            height=32,
        )
        self.btn_comando_manual.pack(fill="x", padx=14, pady=(0, 12))

        # --------------------------------------------------------------
        # 3. Parte Inferior (Ações e Aplicação)
        # --------------------------------------------------------------
        frame_acoes = ctk.CTkFrame(
            corpo,
            fg_color=COR_FRAME,
            corner_radius=10,
            border_width=1,
            border_color=COR_FRAME_BORDA,
            height=55,
        )
        frame_acoes.pack(fill="x", padx=0, pady=0)
        frame_acoes.pack_propagate(False)

        self.btn_ssh_aplicar = ctk.CTkButton(
            frame_acoes,
            text="⚙️  Aplicar Alterações",
            command=self._aplicar_alteracoes_ssh,
            fg_color="#006400",
            hover_color="#008000",
            font=("Segoe UI", 12, "bold"),
            width=180,
            height=38,
            state="disabled",
        )
        self.btn_ssh_aplicar.pack(side="left", padx=12, pady=8)

        self.btn_ssh_recarregar = ctk.CTkButton(
            frame_acoes,
            text="🔄  Recarregar Status",
            command=self._recarregar_portas_ssh,
            fg_color="#233549",
            hover_color="#304760",
            font=FONTE_LABEL,
            width=145,
            height=38,
            state="disabled",
        )
        self.btn_ssh_recarregar.pack(side="left", padx=(0, 10), pady=8)

        self.btn_ssh_cdp = ctk.CTkButton(
            frame_acoes,
            text="🔍  Mapear Vizinhança (CDP)",
            command=self._mapear_vizinhos_cdp,
            fg_color="#1a3b5c",
            hover_color="#234e7a",
            font=FONTE_LABEL,
            width=185,
            height=38,
            state="disabled",
        )
        self.btn_ssh_cdp.pack(side="left", padx=(0, 8), pady=8)

        self.btn_ssh_macs = ctk.CTkButton(
            frame_acoes,
            text="🔍  Rastrear MACs",
            command=self._rastrear_mac_addresses,
            fg_color="#1a4d4d",
            hover_color="#246969",
            font=FONTE_LABEL,
            width=145,
            height=38,
            state="disabled",
        )
        self.btn_ssh_macs.pack(side="left", padx=(0, 8), pady=8)

        self.btn_ssh_rollback = ctk.CTkButton(
            frame_acoes,
            text="🛡️  Ver Rollback",
            command=self._abrir_janela_rollback,
            fg_color="#5c3b1a",
            hover_color="#7a4f23",
            font=FONTE_LABEL_BOLD,
            width=125,
            height=38,
            state="disabled",
        )
        self.btn_ssh_rollback.pack(side="left", padx=(0, 12), pady=8)

        self.lbl_ssh_info_mod = ctk.CTkLabel(
            frame_acoes,
            text="Nenhuma alteração pendente.",
            font=FONTE_LABEL,
            text_color=COR_CINZA,
        )
        self.lbl_ssh_info_mod.pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # Lógica de Conexão e Leitura (Dashboard SSH / COM)
    # ------------------------------------------------------------------

    def _alternar_modo_conexao_dashboard(self, modo: str) -> None:
        """Alterna entre os campos de SSH e Console COM no topo do Dashboard."""
        if "SSH" in modo:
            self.frame_campos_com.pack_forget()
            self.frame_campos_ssh.pack(side="left", padx=(0, 10))
            self.btn_ssh_conectar.configure(text="🔌  Conectar e Ler (SSH)")
        else:
            self.frame_campos_ssh.pack_forget()
            self.frame_campos_com.pack(side="left", padx=(0, 10))
            self.btn_ssh_conectar.configure(text="🔌  Conectar e Ler (COM)")
            self._atualizar_portas_dashboard()

    def _atualizar_portas_dashboard(self) -> None:
        """Atualiza a lista de portas seriais disponíveis no combobox do dashboard."""
        portas = listar_portas()
        if portas:
            self.combo_dash_porta.configure(values=portas)
            porta_atual = self._var_porta.get()
            if porta_atual in portas:
                self._var_dash_com_porta.set(porta_atual)
            else:
                self._var_dash_com_porta.set(portas[0])
        else:
            self.combo_dash_porta.configure(values=["Nenhuma porta"])
            self._var_dash_com_porta.set("Nenhuma porta")

    def _conectar_e_ler_portas_dashboard(self) -> None:
        """Ponto de entrada: dispara conexão e leitura via SSH ou via Console COM."""
        modo = self._var_modo_conexao.get()
        if "SSH" in modo:
            self._conectar_ssh_e_ler_portas()
        else:
            self._conectar_com_e_ler_portas()

    def _conectar_ssh_e_ler_portas(self) -> None:
        """Inicia o processo assíncrono de conexão SSH e leitura de portas."""
        ip = self._var_ssh_ip.get().strip()
        user = self._var_ssh_user.get().strip()
        pwd = self._var_ssh_pass.get()

        if not ip or not user or not pwd:
            messagebox.showerror("Campos Obrigatórios", "Preencha IP, Username e Password para conectar via SSH.")
            return

        if self._thread_ssh and self._thread_ssh.is_alive():
            messagebox.showwarning("Aguarde", "Uma operação já está em andamento.")
            return

        self.btn_ssh_conectar.configure(state="disabled", text="⏳  Conectando...")
        self.lbl_ssh_status.configure(text="● Conectando via SSH...", text_color=COR_AMARELO)

        self._thread_ssh = threading.Thread(
            target=self._worker_conectar_e_ler,
            args=(ip, user, pwd),
            daemon=True,
        )
        self._thread_ssh.start()

    def _worker_conectar_e_ler(self, ip: str, user: str, pwd: str) -> None:
        """Executado em background thread: conecta e extrai status das portas via SSH."""
        try:
            if self.ssh_client:
                self.ssh_client.desconectar()

            self.ssh_client = CiscoSSHClient(host=ip, username=user, password=pwd)
            self.ssh_client.conectar()
            portas = self.ssh_client.obter_status_portas()
            self._conexao_ativa_dashboard = "SSH"
            self.after(0, self._on_conexao_ssh_sucesso, portas)
        except Exception as exc:
            self.after(0, self._on_conexao_ssh_falha, str(exc))

    def _conectar_com_e_ler_portas(self) -> None:
        """Inicia o processo assíncrono de leitura de portas via Console Serial (COM)."""
        if self._thread_ssh and self._thread_ssh.is_alive():
            messagebox.showwarning("Aguarde", "Uma operação já está em andamento.")
            return

        porta = self._var_dash_com_porta.get()
        baud_str = self._var_dash_com_baud.get()

        if not porta or porta == "Nenhuma porta":
            messagebox.showerror("Erro", "Selecione uma porta COM válida.")
            return

        try:
            baudrate = int(baud_str)
        except ValueError:
            messagebox.showerror("Erro", f"Baudrate inválido: {baud_str}")
            return

        self.btn_ssh_conectar.configure(state="disabled", text="⏳  Lendo via COM...")
        self.lbl_ssh_status.configure(text="● Lendo via Console Serial...", text_color=COR_AMARELO)

        self._thread_ssh = threading.Thread(
            target=self._worker_ler_portas_com,
            args=(porta, baudrate),
            daemon=True,
        )
        self._thread_ssh.start()

    def _worker_ler_portas_com(self, porta: str, baudrate: int) -> None:
        """Executado em background thread: garante conexão serial, envia show interfaces status e faz parse."""
        try:
            # 1. Se a porta serial não estiver conectada, estabelece a conexão
            if not self._serial_client or not self._serial_client.conectado or not self.serial_conn or not self.serial_conn.is_open:
                self._serial_client = CiscoSerial(
                    porta=porta,
                    baudrate=baudrate,
                    callback_log=self._callback_log_serial,
                )
                self._serial_client.conectar()
                self.serial_conn = self._serial_client.serial_conn
                threading.Thread(target=self.leitura_continua_serial, daemon=True).start()
                self.after(0, self._atualizar_status_ui, True)

            # 2. Acorda o switch e desativa a paginação
            self._buffer_captura_serial = ""
            self._captura_serial_ativa = True

            with self._serial_client._lock:
                self.serial_conn.write(b"\r\nterminal length 0\r\n")
                self.serial_conn.flush()
            time.sleep(0.6)

            self._buffer_captura_serial = ""

            # 3. Envia show interfaces status
            with self._serial_client._lock:
                self.serial_conn.write(b"show interfaces status\r\n")
                self.serial_conn.flush()

            # 4. Aguarda a recepção completa dos dados
            prazo = time.monotonic() + 4.5
            while time.monotonic() < prazo:
                time.sleep(0.3)
                buff = self._buffer_captura_serial.strip()
                if buff and (buff.endswith("#") or buff.endswith(">")):
                    break

            self._captura_serial_ativa = False
            raw_output = self._buffer_captura_serial

            # 5. Validação de autenticação
            if "username:" in raw_output.lower() or "password:" in raw_output.lower():
                raise RuntimeError("O switch está exigindo autenticação no console. Faça login na aba Configuração Padrão antes de ler as portas.")

            portas = parse_interfaces_status(raw_output)
            if not portas:
                raise RuntimeError("Nenhuma interface foi identificada na resposta do switch. Verifique se o equipamento está em modo privilegiado (#).")

            self._conexao_ativa_dashboard = "COM"
            self.after(0, self._on_conexao_ssh_sucesso, portas)
        except Exception as exc:
            self._captura_serial_ativa = False
            self.after(0, self._on_conexao_ssh_falha, str(exc))

    def _on_conexao_ssh_sucesso(self, portas: List[dict]) -> None:
        """Executado na main thread: atualiza a interface com as portas obtidas."""
        modo = self._var_modo_conexao.get()
        txt_btn = "🔌  Conectar e Ler (COM)" if "COM" in modo else "🔌  Conectar e Ler (SSH)"
        self.btn_ssh_conectar.configure(state="normal", text=txt_btn)
        self.btn_ssh_desconectar.configure(state="normal")
        self.btn_ssh_recarregar.configure(state="normal")
        total = len(portas)
        origem = "Console COM" if self._conexao_ativa_dashboard == "COM" else "SSH"
        self.lbl_ssh_status.configure(text=f"● Conectado ({origem}: {total} portas)", text_color=COR_VERDE)
        self._renderizar_tabela_portas(portas)

    def _on_conexao_ssh_falha(self, erro_msg: str) -> None:
        """Executado na main thread caso a conexão falhe."""
        modo = self._var_modo_conexao.get()
        txt_btn = "🔌  Conectar e Ler (COM)" if "COM" in modo else "🔌  Conectar e Ler (SSH)"
        self.btn_ssh_conectar.configure(state="normal", text=txt_btn)
        self.btn_ssh_desconectar.configure(state="disabled")
        self.btn_ssh_recarregar.configure(state="disabled")
        self.btn_ssh_aplicar.configure(state="disabled")
        self.lbl_ssh_status.configure(text="● Erro de Conexão", text_color=COR_VERMELHO)
        messagebox.showerror("Falha de Conexão", f"Não foi possível ler as portas do switch:\n\n{erro_msg}")

    def _desconectar_dashboard(self) -> None:
        """Encerra a sessão ativa do dashboard (SSH ou COM) e limpa a tabela."""
        if self._conexao_ativa_dashboard == "SSH" and self.ssh_client:
            self.ssh_client.desconectar()
            self.ssh_client = None

        self._conexao_ativa_dashboard = None
        self.btn_ssh_desconectar.configure(state="disabled")
        self.btn_ssh_recarregar.configure(state="disabled")
        self.btn_ssh_cdp.configure(state="disabled")
        if self.btn_ssh_macs:
            self.btn_ssh_macs.configure(state="disabled")
        self.btn_ssh_aplicar.configure(state="disabled")
        self.lbl_ssh_status.configure(text="● Desconectado", text_color=COR_VERMELHO)
        self.lbl_ssh_info_mod.configure(text="Nenhuma alteração pendente.")

        # Limpa tabela
        for w in self._frame_tabela_portas.winfo_children():
            w.destroy()
        self._lbl_tabela_vazia = ctk.CTkLabel(
            self._frame_tabela_portas,
            text="Sessão encerrada.",
            font=("Segoe UI", 12),
            text_color=COR_CINZA,
        )
        self._lbl_tabela_vazia.pack(expand=True, pady=60)
        self._linhas_portas.clear()
        self.atualizar_preview_comandos()

    # ------------------------------------------------------------------
    # Construção Visual das Linhas das Portas (Dashboard Dinâmico V1.5)
    # ------------------------------------------------------------------

    def _renderizar_tabela_portas(self, portas: List[dict]) -> None:
        """Desenha cada linha de porta de forma interativa no ScrollableFrame."""
        for w in self._frame_tabela_portas.winfo_children():
            w.destroy()
        self._linhas_portas.clear()

        if not portas:
            ctk.CTkLabel(
                self._frame_tabela_portas,
                text="Nenhuma porta retornada pelo switch.",
                font=("Segoe UI", 12),
                text_color=COR_CINZA,
            ).pack(expand=True, pady=60)
            return

        self.btn_ssh_cdp.configure(state="normal")
        self.btn_ssh_macs.configure(state="normal")
        self.btn_ssh_recarregar.configure(state="normal")

        for p in portas:
            # Cor do indicador de status do link
            st_lower = p["status"].lower()
            if "connected" in st_lower and "notconnect" not in st_lower:
                cor_status = COR_VERDE
            elif "notconnect" in st_lower:
                cor_status = COR_CINZA
            else:
                cor_status = COR_VERMELHO

            # Container da linha (Row)
            row = ctk.CTkFrame(self._frame_tabela_portas, fg_color="#121824", corner_radius=6, height=36)
            row.pack(fill="x", pady=2, padx=2)
            row.pack_propagate(False)

            # CheckBox para seleção de configuração em lote (Bulk)
            chk_sel = ctk.CTkCheckBox(
                row,
                text="",
                width=24,
                checkbox_width=18,
                checkbox_height=18,
                fg_color=COR_DESTAQUE,
            )
            chk_sel.pack(side="left", padx=(6, 2))

            # Nome da Porta
            lbl_porta = ctk.CTkLabel(
                row,
                text=p["porta"],
                width=68,
                font=("Consolas", 11, "bold"),
                text_color=COR_DESTAQUE,
                anchor="w",
            )
            lbl_porta.pack(side="left", padx=2)

            # Status do Link
            lbl_status = ctk.CTkLabel(
                row,
                text=p["status"],
                width=68,
                font=("Segoe UI", 10, "bold"),
                text_color=cor_status,
                anchor="w",
            )
            lbl_status.pack(side="left", padx=2)

            # MAC Address (Camada 2)
            norm_nome = normalize_interface_name(p["porta"])
            macs_lista = self._mac_por_porta.get(norm_nome) or self._mac_por_porta.get(p["porta"]) or []
            if macs_lista:
                txt_mac = ", ".join(macs_lista[:1]) + (f" (+{len(macs_lista)-1})" if len(macs_lista) > 1 else "")
                cor_mac = "#00e5ff"
            else:
                txt_mac = "-"
                cor_mac = COR_CINZA

            lbl_mac = ctk.CTkLabel(
                row,
                text=txt_mac,
                width=125,
                font=("Consolas", 10),
                text_color=cor_mac,
                anchor="w",
            )
            lbl_mac.pack(side="left", padx=2)

            # Vizinhança (CDP)
            cdp_info = self._cdp_vizinhos.get(norm_nome) or self._cdp_vizinhos.get(p["porta"])
            if cdp_info:
                txt_cdp = f"{cdp_info['device_id']} ({cdp_info['platform']})"
                cor_cdp = "#00e5ff"
            else:
                txt_cdp = "-"
                cor_cdp = COR_CINZA

            lbl_cdp = ctk.CTkLabel(
                row,
                text=txt_cdp,
                width=125,
                font=("Segoe UI", 9, "bold"),
                text_color=cor_cdp,
                anchor="w",
            )
            lbl_cdp.pack(side="left", padx=2)

            # Descrição (Editável)
            entry_desc = ctk.CTkEntry(
                row,
                font=FONTE_LABEL,
                fg_color="#0d1117",
                border_color=COR_FRAME_BORDA,
                width=120,
                height=28,
            )
            entry_desc.pack(side="left", padx=2)
            entry_desc.insert(0, p["descricao"])

            # Modo (Access / Trunk)
            opt_modo = ctk.CTkOptionMenu(
                row,
                values=["Access", "Trunk"],
                width=75,
                height=28,
                fg_color="#21262d",
                button_color="#30363d",
                font=FONTE_LABEL,
            )
            opt_modo.pack(side="left", padx=2)
            opt_modo.set(p["modo"])

            # VLAN (Editável: número para Access ou lista para Trunk)
            entry_vlan = ctk.CTkEntry(
                row,
                font=FONTE_LABEL,
                fg_color="#0d1117",
                border_color=COR_FRAME_BORDA,
                width=75,
                height=28,
            )
            entry_vlan.pack(side="left", padx=2)
            entry_vlan.insert(0, str(p["vlan"]))

            # Estado Administrativo (Ativa / Desativada)
            is_active = p.get("ativo", True)
            if "status" in p and p["status"].lower() in ("disabled", "err-disabled"):
                is_active = False

            estado_inicial = "Ativa (no shutdown)" if is_active else "Desativada (shutdown)"

            opt_estado = ctk.CTkOptionMenu(
                row,
                values=["Ativa (no shutdown)", "Desativada (shutdown)"],
                width=130,
                height=28,
                fg_color="#21262d",
                button_color="#30363d",
                font=FONTE_LABEL,
            )
            opt_estado.pack(side="left", padx=2)
            opt_estado.set(estado_inicial)

            # PoE (Power over Ethernet)
            opt_poe = ctk.CTkOptionMenu(
                row,
                values=["Auto (Ligado)", "Never (Desligado)"],
                width=120,
                height=28,
                fg_color="#21262d",
                button_color="#30363d",
                font=FONTE_LABEL,
            )
            opt_poe.pack(side="left", padx=2)
            opt_poe.set("Auto (Ligado)")

            # Velocidade
            lbl_speed = ctk.CTkLabel(
                row,
                text=p["velocidade"] or "-",
                width=50,
                font=("Segoe UI", 10),
                text_color=COR_CINZA,
                anchor="w",
            )
            lbl_speed.pack(side="left", padx=(2, 4))

            # Dicionário de rastreio da linha
            linha_info = {
                "porta": p["porta"],
                "row_frame": row,
                "chk_sel": chk_sel,
                "lbl_mac": lbl_mac,
                "lbl_cdp": lbl_cdp,
                "entry_desc": entry_desc,
                "opt_modo": opt_modo,
                "entry_vlan": entry_vlan,
                "opt_estado": opt_estado,
                "opt_poe": opt_poe,
                "orig_desc": p["descricao"],
                "orig_modo": p["modo"],
                "orig_vlan": str(p["vlan"]),
                "orig_estado": estado_inicial,
                "orig_poe": "Auto (Ligado)",
                "modificado": False,
            }

            # Listeners de modificação em tempo real
            def on_mod(event=None, info=linha_info):
                d_atual = info["entry_desc"].get().strip()
                m_atual = info["opt_modo"].get()
                v_atual = info["entry_vlan"].get().strip()
                e_atual = info["opt_estado"].get()
                poe_atual = info["opt_poe"].get()

                modificado = (
                    d_atual != info["orig_desc"] or
                    m_atual != info["orig_modo"] or
                    v_atual != info["orig_vlan"] or
                    e_atual != info["orig_estado"] or
                    poe_atual != info["orig_poe"]
                )
                info["modificado"] = modificado
                if modificado:
                    info["row_frame"].configure(fg_color="#1a3353")  # Destaque de linha modificada
                else:
                    info["row_frame"].configure(fg_color="#121824")
                self._atualizar_contador_modificacoes()
                self.atualizar_preview_comandos()

            entry_desc.bind("<KeyRelease>", on_mod)
            entry_vlan.bind("<KeyRelease>", on_mod)
            opt_modo.configure(command=lambda m, info=linha_info: on_mod(info=info))
            opt_estado.configure(command=lambda e, info=linha_info: on_mod(info=info))
            opt_poe.configure(command=lambda pe, info=linha_info: on_mod(info=info))

            self._linhas_portas.append(linha_info)

        self._atualizar_contador_modificacoes()
        self.atualizar_preview_comandos()

    def _toggle_selecionar_todas_portas(self) -> None:
        """Marca ou desmarca todas as caixas de seleção 'Sel' das portas."""
        marcar = self._var_sel_todos.get()
        for item in self._linhas_portas:
            if marcar:
                item["chk_sel"].select()
            else:
                item["chk_sel"].deselect()

    def _aplicar_configuracao_lote(self) -> None:
        """Aplica os parâmetros da barra 'Lote' em todas as portas selecionadas na tabela."""
        selecionadas = [item for item in self._linhas_portas if item["chk_sel"].get() == 1]
        if not selecionadas:
            messagebox.showinfo(
                "Configuração em Lote",
                "Nenhuma porta selecionada.\n\n"
                "Marque a caixa 'Sel' de uma ou mais portas (ou 'Todas') antes de aplicar em lote.",
            )
            return

        novo_modo = self._var_bulk_modo.get()
        nova_vlan = self._var_bulk_vlan.get().strip() or "1"
        nova_desc = self._var_bulk_desc.get().strip()
        novo_estado = self._var_bulk_estado.get()
        novo_poe = self._var_bulk_poe.get()

        for item in selecionadas:
            item["opt_modo"].set(novo_modo)
            item["entry_vlan"].delete(0, "end")
            item["entry_vlan"].insert(0, nova_vlan)
            item["opt_estado"].set(novo_estado)
            item["opt_poe"].set(novo_poe)
            if nova_desc:
                item["entry_desc"].delete(0, "end")
                item["entry_desc"].insert(0, nova_desc)

            d_atual = item["entry_desc"].get().strip()
            m_atual = item["opt_modo"].get()
            v_atual = item["entry_vlan"].get().strip()
            e_atual = item["opt_estado"].get()
            poe_atual = item["opt_poe"].get()

            modificado = (
                d_atual != item["orig_desc"] or
                m_atual != item["orig_modo"] or
                v_atual != item["orig_vlan"] or
                e_atual != item["orig_estado"] or
                poe_atual != item["orig_poe"]
            )
            item["modificado"] = modificado
            if modificado:
                item["row_frame"].configure(fg_color="#1a3353")
            else:
                item["row_frame"].configure(fg_color="#121824")

        self._atualizar_contador_modificacoes()
        self.atualizar_preview_comandos()
        self._log_terminal(f"Configuração em lote aplicada na tabela para {len(selecionadas)} porta(s).", "sucesso")

    def _atualizar_contador_modificacoes(self) -> None:
        """Conta quantas portas foram modificadas e atualiza o botão Aplicar."""
        total_mod = sum(1 for item in self._linhas_portas if item.get("modificado", False))

        if total_mod > 0:
            self.lbl_ssh_info_mod.configure(
                text=f"⚠️  {total_mod} porta(s) modificada(s) pronta(s) para aplicação.",
                text_color=COR_AMARELO,
            )
            self.btn_ssh_aplicar.configure(state="normal")
        else:
            self.lbl_ssh_info_mod.configure(
                text="Nenhuma alteração pendente nas portas.",
                text_color=COR_CINZA,
            )
            self.btn_ssh_aplicar.configure(state="disabled")

    def atualizar_preview_comandos(self, *args) -> None:
        """
        Varre todas as linhas da tabela de portas, identifica as portas
        modificadas e gera a pré-visualização em tempo real dos comandos
        Cisco IOS, agrupando portas com configurações idênticas em 'interface range'.
        """
        if not hasattr(self, "textbox_preview_ssh") or not self.textbox_preview_ssh:
            return

        comandos_totais: List[str] = []
        grupos_alteracoes: dict = {}

        for item in self._linhas_portas:
            if item.get("modificado", False):
                porta = item["porta"]
                desc = item["entry_desc"].get().strip()
                modo = item["opt_modo"].get()
                vlan = item["entry_vlan"].get().strip() or "1"
                is_active = (item["opt_estado"].get() == "Ativa (no shutdown)")
                poe_val = item["opt_poe"].get()
                poe_param = "never" if "never" in poe_val.lower() else "auto"
                chave = (modo, vlan, desc, is_active, poe_param)
                if chave not in grupos_alteracoes:
                    grupos_alteracoes[chave] = []
                grupos_alteracoes[chave].append(porta)

        for (modo, vlan, desc, is_active, poe_param), portas_grupo in grupos_alteracoes.items():
            if len(portas_grupo) > 1:
                # Otimização por 'interface range' (Cisco IOS)
                if modo == "Access":
                    cmds = CiscoTemplates.get_bulk_access_config(
                        interfaces=portas_grupo,
                        vlan_dados=vlan,
                        description=desc,
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
                else:
                    cmds = CiscoTemplates.get_bulk_trunk_config(
                        interfaces=portas_grupo,
                        allowed_vlans=vlan,
                        description=desc or "Uplink",
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
            else:
                porta = portas_grupo[0]
                if modo == "Access":
                    cmds = CiscoTemplates.get_access_port_config(
                        interface=porta,
                        vlan_dados=vlan,
                        description=desc,
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
                else:
                    cmds = CiscoTemplates.get_trunk_port_config(
                        interface=porta,
                        allowed_vlans=vlan,
                        description=desc or "Uplink",
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
            comandos_totais.extend(cmds)

        self.textbox_preview_ssh.configure(state="normal")
        self.textbox_preview_ssh.delete("1.0", "end")

        if comandos_totais:
            total_portas_mod = sum(len(p) for p in grupos_alteracoes.values())
            texto = "! ======================================\n"
            texto += f"! LIVE PREVIEW: {len(comandos_totais)} comando(s) para {total_portas_mod} porta(s) (Range/Otimizado)\n"
            texto += "! ======================================\n"
            texto += "\n".join(comandos_totais) + "\n"
            self.textbox_preview_ssh.insert("1.0", texto)
        else:
            msg_vazio = (
                "! Console de Pré-visualização\n"
                "!\n"
                "! Nenhuma alteração pendente.\n"
                "! Modifique qualquer porta na tabela\n"
                "! ou utilize a barra '⚡ Lote' para pré-visualizar\n"
                "! os comandos 'interface range' gerados.\n"
            )
            self.textbox_preview_ssh.insert("1.0", msg_vazio)

        self.textbox_preview_ssh.configure(state="disabled")

    def _mapear_vizinhos_cdp(self) -> None:
        """Dispara o mapeamento CDP assíncrono."""
        if self._conexao_ativa_dashboard == "COM":
            if not self._serial_client or not self._serial_client.conectado:
                messagebox.showerror("Sem Conexão", "A porta serial não está conectada.")
                return
        else:
            if not self.ssh_client or not self.ssh_client.conectado:
                messagebox.showerror("Sem Conexão", "Conecte-se via SSH ou COM antes de mapear vizinhos.")
                return

        self.btn_ssh_cdp.configure(state="disabled", text="⏳  Mapeando CDP...")
        threading.Thread(target=self._worker_mapear_cdp, daemon=True).start()

    def _worker_mapear_cdp(self) -> None:
        """Executa 'show cdp neighbors' via SSH ou COM e processa os dados."""
        try:
            if self._conexao_ativa_dashboard == "COM":
                with self._serial_client._lock:
                    self.serial_conn.write(b"\r\nterminal length 0\r\nshow cdp neighbors\r\n")
                    self.serial_conn.flush()
                time.sleep(2.0)
                raw_output = ""
                prazo = time.monotonic() + 4.0
                while time.monotonic() < prazo:
                    time.sleep(0.3)
                    if self.serial_conn.in_waiting > 0:
                        raw_output += self.serial_conn.read(self.serial_conn.in_waiting).decode("utf-8", errors="ignore")
                    if "#" in raw_output:
                        break
                vizinhos = parse_cdp_neighbors(raw_output)
            else:
                vizinhos = self.ssh_client.obter_vizinhos_cdp()

            self.after(0, self._aplicar_dados_cdp_na_tabela, vizinhos)
        except Exception as exc:
            self.after(0, self._on_cdp_falha, str(exc))

    def _aplicar_dados_cdp_na_tabela(self, vizinhos: dict) -> None:
        """Atualiza a coluna de vizinhança na tabela visual."""
        self._cdp_vizinhos = vizinhos
        total_mapeados = 0

        for item in self._linhas_portas:
            porta = item["porta"]
            norm = normalize_interface_name(porta)
            info = vizinhos.get(norm) or vizinhos.get(porta) or vizinhos.get(porta.lower())

            if info:
                dev = info.get("device_id", "")
                plat = info.get("platform", "")
                texto_cdp = f"{dev} ({plat})" if plat else dev
                item["lbl_cdp"].configure(text=texto_cdp, text_color="#00e5ff")
                total_mapeados += 1
            else:
                item["lbl_cdp"].configure(text="-", text_color=COR_CINZA)

        self.btn_ssh_cdp.configure(state="normal", text="🔍  Mapear Vizinhança (CDP)")
        self._log_terminal(f"CDP: {total_mapeados} vizinho(s) identificado(s) no switch.", "sucesso")
        messagebox.showinfo("CDP Concluído", f"Mapeamento de Vizinhança (CDP) concluído!\n\nTotal de vizinhos mapeados: {total_mapeados}")

    def _on_cdp_falha(self, erro_msg: str) -> None:
        """Trata erro no mapeamento CDP."""
        self.btn_ssh_cdp.configure(state="normal", text="🔍  Mapear Vizinhança (CDP)")
        messagebox.showerror("Erro no CDP", f"Falha ao executar 'show cdp neighbors':\n\n{erro_msg}")

    # ------------------------------------------------------------------
    # NMS — Rastreio de MAC Address (Camada 2)
    # ------------------------------------------------------------------

    def _rastrear_mac_addresses(self) -> None:
        """Dispara o rastreamento de MAC addresses (Camada 2) em background."""
        if self._conexao_ativa_dashboard == "COM":
            if not self._serial_client or not self._serial_client.conectado:
                messagebox.showerror("Sem Conexão", "A porta serial não está conectada.")
                return
        else:
            if not self.ssh_client or not self.ssh_client.conectado:
                messagebox.showerror("Sem Conexão", "Conecte-se via SSH ou COM antes de rastrear MAC addresses.")
                return

        self.btn_ssh_macs.configure(state="disabled", text="⏳  Rastreando MACs...")
        threading.Thread(target=self._worker_rastrear_macs, daemon=True).start()

    def _worker_rastrear_macs(self) -> None:
        """Executa 'show mac address-table' via SSH ou COM e processa os dados."""
        try:
            if self._conexao_ativa_dashboard == "COM":
                with self._serial_client._lock:
                    self.serial_conn.write(b"\r\nterminal length 0\r\nshow mac address-table\r\n")
                    self.serial_conn.flush()
                time.sleep(2.0)
                raw_output = ""
                prazo = time.monotonic() + 4.0
                while time.monotonic() < prazo:
                    time.sleep(0.3)
                    if self.serial_conn.in_waiting > 0:
                        raw_output += self.serial_conn.read(self.serial_conn.in_waiting).decode("utf-8", errors="ignore")
                    if "#" in raw_output:
                        break
                # Fallback para IOS legados caso a tabela venha vazia ou com erro de sintaxe
                if "% Invalid input detected" in raw_output or not raw_output.strip():
                    with self._serial_client._lock:
                        self.serial_conn.write(b"\r\nshow mac-address-table\r\n")
                        self.serial_conn.flush()
                    time.sleep(2.0)
                    prazo = time.monotonic() + 4.0
                    while time.monotonic() < prazo:
                        time.sleep(0.3)
                        if self.serial_conn.in_waiting > 0:
                            raw_output += self.serial_conn.read(self.serial_conn.in_waiting).decode("utf-8", errors="ignore")
                        if "#" in raw_output:
                            break
                macs_dict = parse_mac_address_table(raw_output)
            else:
                macs_dict = self.ssh_client.obter_mac_addresses()

            self.after(0, self._aplicar_dados_mac_na_tabela, macs_dict)
        except Exception as exc:
            self.after(0, self._on_mac_falha, str(exc))

    def _aplicar_dados_mac_na_tabela(self, macs_dict: Dict[str, List[str]]) -> None:
        """Atualiza a coluna de MAC address na tabela visual das portas."""
        self._mac_por_porta = macs_dict
        total_portas_com_mac = 0
        total_macs = sum(len(m) for m in macs_dict.values())

        for item in self._linhas_portas:
            porta = item["porta"]
            norm = normalize_interface_name(porta)
            macs = macs_dict.get(norm) or macs_dict.get(porta) or macs_dict.get(porta.lower())

            if macs:
                if len(macs) == 1:
                    texto_mac = macs[0]
                elif len(macs) == 2:
                    texto_mac = f"{macs[0]}, {macs[1]}"
                else:
                    texto_mac = f"{macs[0]} (+{len(macs)-1})"
                item["lbl_mac"].configure(text=texto_mac, text_color="#00e5ff")
                total_portas_com_mac += 1
            else:
                item["lbl_mac"].configure(text="-", text_color=COR_CINZA)

        self.btn_ssh_macs.configure(state="normal", text="🔍  Rastrear MACs")
        self._log_terminal(f"MACs: {total_macs} MAC(s) rastreado(s) em {total_portas_com_mac} porta(s).", "sucesso")
        messagebox.showinfo(
            "Rastreio de MAC Concluído",
            f"Rastreamento de MAC Address (Camada 2) concluído!\n\n"
            f"• Total de MACs encontrados: {total_macs}\n"
            f"• Portas ativas com endereços: {total_portas_com_mac}",
        )

    def _on_mac_falha(self, erro_msg: str) -> None:
        """Trata falha no rastreamento de MAC addresses."""
        self.btn_ssh_macs.configure(state="normal", text="🔍  Rastrear MACs")
        messagebox.showerror("Erro no Rastreio de MAC", f"Falha ao obter tabela de MAC addresses:\n\n{erro_msg}")

    def _recarregar_portas_ssh(self) -> None:
        """Reler o status das interfaces do switch via conexão ativa (SSH ou COM)."""
        if self._conexao_ativa_dashboard == "COM":
            if not self._serial_client or not self._serial_client.conectado:
                messagebox.showerror("Sem Conexão", "A porta serial não está conectada.")
                return
            self.btn_ssh_recarregar.configure(state="disabled", text="⏳  Lendo...")
            porta = self._var_dash_com_porta.get()
            baud = int(self._var_dash_com_baud.get() or "9600")
            threading.Thread(target=self._worker_ler_portas_com, args=(porta, baud), daemon=True).start()
        else:
            if not self.ssh_client or not self.ssh_client.conectado:
                messagebox.showerror("Sem Conexão", "Conecte-se via SSH antes de recarregar.")
                return
            self.btn_ssh_recarregar.configure(state="disabled", text="⏳  Lendo...")
            threading.Thread(target=self._worker_recarregar_portas, daemon=True).start()

    def _worker_recarregar_portas(self) -> None:
        try:
            portas = self.ssh_client.obter_status_portas()
            self.after(0, self._renderizar_tabela_portas, portas)
            self.after(0, self.btn_ssh_recarregar.configure, {"state": "normal", "text": "🔄  Recarregar Status"})
        except Exception as exc:
            self.after(0, messagebox.showerror, "Erro", f"Falha ao reler portas:\n{exc}")
            self.after(0, self.btn_ssh_recarregar.configure, {"state": "normal", "text": "🔄  Recarregar Status"})

    # ------------------------------------------------------------------
    # Aplicação de Alterações nas Portas com Snapshot de Rollback
    # ------------------------------------------------------------------

    def _aplicar_alteracoes_ssh(self) -> None:
        """Coleta as portas modificadas e envia as configurações via SSH ou COM com snapshot de segurança."""
        if self._conexao_ativa_dashboard == "COM":
            if not self._serial_client or not self._serial_client.conectado:
                messagebox.showerror("Sem Conexão", "Não há conexão serial ativa.")
                return
        else:
            if not self.ssh_client or not self.ssh_client.conectado:
                messagebox.showerror("Sem Conexão", "Não há conexão SSH ativa.")
                return

        comandos_totais: List[str] = []
        portas_afetadas: List[str] = []
        grupos_alteracoes: dict = {}

        for item in self._linhas_portas:
            if item.get("modificado", False):
                porta = item["porta"]
                desc = item["entry_desc"].get().strip()
                modo = item["opt_modo"].get()
                vlan = item["entry_vlan"].get().strip() or "1"
                is_active = (item["opt_estado"].get() == "Ativa (no shutdown)")
                poe_val = item["opt_poe"].get()
                poe_param = "never" if "never" in poe_val.lower() else "auto"
                chave = (modo, vlan, desc, is_active, poe_param)
                if chave not in grupos_alteracoes:
                    grupos_alteracoes[chave] = []
                grupos_alteracoes[chave].append(porta)
                portas_afetadas.append(porta)

        for (modo, vlan, desc, is_active, poe_param), portas_grupo in grupos_alteracoes.items():
            if len(portas_grupo) > 1:
                if modo == "Access":
                    cmds = CiscoTemplates.get_bulk_access_config(
                        interfaces=portas_grupo,
                        vlan_dados=vlan,
                        description=desc,
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
                else:
                    cmds = CiscoTemplates.get_bulk_trunk_config(
                        interfaces=portas_grupo,
                        allowed_vlans=vlan,
                        description=desc or "Uplink",
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
            else:
                porta = portas_grupo[0]
                if modo == "Access":
                    cmds = CiscoTemplates.get_access_port_config(
                        interface=porta,
                        vlan_dados=vlan,
                        description=desc,
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
                else:
                    cmds = CiscoTemplates.get_trunk_port_config(
                        interface=porta,
                        allowed_vlans=vlan,
                        description=desc or "Uplink",
                        is_active=is_active,
                        poe_mode=poe_param,
                    )
            comandos_totais.extend(cmds)

        if not comandos_totais or not portas_afetadas:
            messagebox.showinfo("Sem Alterações", "Nenhuma porta marcada para alteração.")
            return

        resumo = ", ".join(portas_afetadas[:8])
        if len(portas_afetadas) > 8:
            resumo += f" ... (+{len(portas_afetadas) - 8})"

        origem = "Console Serial (COM)" if self._conexao_ativa_dashboard == "COM" else "SSH"
        confirma = messagebox.askyesno(
            f"Confirmar Aplicação ({origem})",
            f"Deseja aplicar as configurações em {len(portas_afetadas)} porta(s) via {origem}?\n\n"
            f"Portas afetadas: {resumo}\n"
            f"Total de comandos: {len(comandos_totais)}\n\n"
            f"Um Snapshot de Segurança (Rollback) será capturado automaticamente antes da aplicação.",
        )
        if not confirma:
            return

        self.btn_ssh_aplicar.configure(state="disabled", text="⏳  Capturando Snapshot...")
        self.btn_ssh_recarregar.configure(state="disabled")

        if self._conexao_ativa_dashboard == "COM":
            self._thread_ssh = threading.Thread(
                target=self._worker_aplicar_com,
                args=(comandos_totais, portas_afetadas),
                daemon=True,
            )
        else:
            self._thread_ssh = threading.Thread(
                target=self._worker_aplicar_ssh,
                args=(comandos_totais, portas_afetadas),
                daemon=True,
            )
        self._thread_ssh.start()

    def _worker_aplicar_ssh(self, comandos: List[str], portas_afetadas: List[str]) -> None:
        """Captura snapshot, envia comandos via Netmiko e atualiza a interface."""
        try:
            # 1. Snapshot pré-alteração
            self.after(0, self.btn_ssh_aplicar.configure, {"text": "⏳ Snapshot..."})
            snapshot = self.ssh_client.obter_snapshot_interfaces(portas_afetadas)
            self._ultimo_snapshot_rollback = snapshot
            self._timestamp_rollback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.after(0, self.btn_ssh_rollback.configure, {"state": "normal"})
            self.after(0, self._log_terminal, f"Snapshot de segurança criado para {len(portas_afetadas)} porta(s).", "info")

            # 2. Aplicação
            self.after(0, self.btn_ssh_aplicar.configure, {"text": "⏳ Aplicando..."})
            resultado = self.ssh_client.enviar_configuracoes(comandos)
            portas_atualizadas = self.ssh_client.obter_status_portas()
            self.after(0, self._on_aplicacao_ssh_sucesso, len(comandos), portas_atualizadas, "SSH")
        except Exception as exc:
            self.after(0, self._on_aplicacao_ssh_falha, str(exc))

    def _worker_aplicar_com(self, comandos: List[str], portas_afetadas: List[str]) -> None:
        """Captura snapshot, aplica comandos via Porta Serial (COM), salva e atualiza."""
        try:
            # 1. Snapshot pré-alteração via COM
            self.after(0, self.btn_ssh_aplicar.configure, {"text": "⏳ Snapshot..."})
            snapshot = {}
            for p in portas_afetadas:
                with self._serial_client._lock:
                    self.serial_conn.write(f"show running-config interface {p}\r\n".encode("utf-8"))
                    self.serial_conn.flush()
                time.sleep(1.0)
                saida = ""
                prazo = time.monotonic() + 3.0
                while time.monotonic() < prazo:
                    time.sleep(0.2)
                    if self.serial_conn.in_waiting > 0:
                        saida += self.serial_conn.read(self.serial_conn.in_waiting).decode("utf-8", errors="ignore")
                    if "#" in saida:
                        break
                snapshot[p] = saida or f"! interface {p}\n! Sem retorno da running-config"

            self._ultimo_snapshot_rollback = snapshot
            self._timestamp_rollback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.after(0, self.btn_ssh_rollback.configure, {"state": "normal"})
            self.after(0, self._log_terminal, f"Snapshot de segurança criado para {len(portas_afetadas)} porta(s).", "info")

            # 2. Aplicação com configure terminal
            with self._serial_client._lock:
                self.serial_conn.write(b"\r\nconfigure terminal\r\n")
                self.serial_conn.flush()
            time.sleep(0.5)

            total = len(comandos)
            for idx, cmd in enumerate(comandos, 1):
                pct = int(idx / total * 100)
                self.after(0, self.btn_ssh_aplicar.configure, {"text": f"⏳ {pct}% ({idx}/{total})"})
                with self._serial_client._lock:
                    self.serial_conn.write(f"{cmd}\r\n".encode("utf-8", errors="replace"))
                    self.serial_conn.flush()
                time.sleep(0.3)

            with self._serial_client._lock:
                self.serial_conn.write(b"end\r\nwrite memory\r\n")
                self.serial_conn.flush()
            time.sleep(1.5)

            # 3. Reler status após aplicar
            porta = self._var_dash_com_porta.get()
            baud = int(self._var_dash_com_baud.get() or "9600")
            self._worker_ler_portas_com(porta, baud)
            self.after(0, self._on_aplicacao_ssh_sucesso, total, None, "Console Serial (COM)")
        except Exception as exc:
            self.after(0, self._on_aplicacao_ssh_falha, str(exc))

    def _abrir_janela_rollback(self) -> None:
        """Abre janela com a configuração anterior para auditoria e reversão (Rollback)."""
        if not self._ultimo_snapshot_rollback:
            messagebox.showinfo(
                "Rollback",
                "Nenhum snapshot de segurança disponível no momento.\n\n"
                "O snapshot é capturado automaticamente sempre antes de aplicar alterações nas portas.",
            )
            return

        janela = ctk.CTkToplevel(self)
        janela.title("🛡️ Snapshot de Segurança & Rollback")
        janela.geometry("750x550")
        janela.minsize(600, 400)
        janela.transient(self)
        janela.focus_set()

        frame_top = ctk.CTkFrame(janela, fg_color="transparent")
        frame_top.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            frame_top,
            text="🛡️ Configuração Original Pré-Alteração (Rollback)",
            font=FONTE_TITULO,
            text_color=COR_DESTAQUE,
        ).pack(anchor="w")

        ctk.CTkLabel(
            frame_top,
            text=f"Capturado em: {self._timestamp_rollback} | Total de portas: {len(self._ultimo_snapshot_rollback)}",
            font=FONTE_LABEL,
            text_color=COR_CINZA,
        ).pack(anchor="w")

        texto_rollback = (
            f"! ==============================================================================\n"
            f"! SNAPSHOT DE SEGURANÇA (ROLLBACK) - PROVISIONADOR DE SWITCH\n"
            f"! Data/Hora: {self._timestamp_rollback}\n"
            f"! Portas afetadas: {', '.join(self._ultimo_snapshot_rollback.keys())}\n"
            f"! ==============================================================================\n"
            f"! Para reverter as alterações, copie os comandos abaixo e cole no terminal\n"
            f"! no modo de configuração global (configure terminal):\n"
            f"! ==============================================================================\n\n"
        )

        for p_nome, p_cfg in self._ultimo_snapshot_rollback.items():
            texto_rollback += f"! --- Interface {p_nome} ---\n"
            texto_rollback += p_cfg.strip() + "\n!\n"

        txt_box = ctk.CTkTextbox(
            janela,
            font=("Consolas", 10),
            fg_color="#0d1117",
            text_color="#00ff88",
            border_color=COR_FRAME_BORDA,
            border_width=1,
            wrap="none",
        )
        txt_box.pack(fill="both", expand=True, padx=16, pady=8)
        txt_box.insert("1.0", texto_rollback)
        txt_box.configure(state="disabled")

        frame_botoes = ctk.CTkFrame(janela, fg_color="transparent")
        frame_botoes.pack(fill="x", padx=16, pady=(8, 16))

        def copiar_rollback():
            self.clipboard_clear()
            self.clipboard_append(texto_rollback)
            messagebox.showinfo("Copiado", "Configuração de rollback copiada para a área de transferência!")

        btn_copiar = ctk.CTkButton(
            frame_botoes,
            text="📋 Copiar Configuração de Rollback",
            command=copiar_rollback,
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
            font=FONTE_LABEL_BOLD,
            height=32,
        )
        btn_copiar.pack(side="left", padx=(0, 10))

        btn_fechar = ctk.CTkButton(
            frame_botoes,
            text="Fechar",
            command=janela.destroy,
            fg_color="#3a3a3a",
            hover_color="#555",
            font=FONTE_LABEL,
            height=32,
            width=80,
        )
        btn_fechar.pack(side="right")

    def _on_aplicacao_ssh_sucesso(self, total_cmds: int, portas: Optional[List[dict]] = None, origem: str = "SSH") -> None:
        self.btn_ssh_aplicar.configure(state="normal", text="⚙️  Aplicar Alterações")
        self.btn_ssh_recarregar.configure(state="normal")
        if portas is not None:
            self._renderizar_tabela_portas(portas)
        messagebox.showinfo(
            "Sucesso",
            f"Configurações aplicadas com sucesso via {origem}!\n"
            f"{total_cmds} comandos executados e salvos na NVRAM.\n"
            f"O status das portas foi recarregado.",
        )

    def _on_aplicacao_ssh_falha(self, erro_msg: str) -> None:
        self.btn_ssh_aplicar.configure(state="normal", text="⚙️  Aplicar Alterações")
        self.btn_ssh_recarregar.configure(state="normal")
        messagebox.showerror("Erro ao Aplicar", f"Falha ao enviar configurações:\n\n{erro_msg}")

    # ------------------------------------------------------------------
    # Envio de Comando Manual Livre (SSH / Serial)
    # ------------------------------------------------------------------

    def _abrir_modal_comando_manual(self) -> None:
        """Abre janela para digitar e executar comandos manuais no switch via SSH ou Serial."""
        # Determina qual conexão está ativa
        origem = self._conexao_ativa_dashboard
        if not origem:
            if self.ssh_client and self.ssh_client.conectado:
                origem = "SSH"
                self._conexao_ativa_dashboard = "SSH"
            elif self._serial_client and self._serial_client.conectado:
                origem = "COM"
                self._conexao_ativa_dashboard = "COM"
            else:
                messagebox.showwarning(
                    "Sem Conexão Ativa",
                    "Conecte-se ao switch via SSH ou Porta Serial (COM) no Dashboard antes de enviar comandos manuais.",
                )
                return

        modal = ctk.CTkToplevel(self)
        modal.title(f"📟 Enviar Comando Manual — Cisco IOS ({origem})")
        modal.geometry("620x520")
        modal.minsize(500, 400)
        modal.configure(fg_color=COR_BG_ESCURO)
        modal.transient(self)
        modal.grab_set()

        modal.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 310
        y = self.winfo_y() + (self.winfo_height() // 2) - 260
        modal.geometry(f"+{max(0, x)}+{max(0, y)}")

        # Cabeçalho
        frame_topo = ctk.CTkFrame(modal, fg_color="transparent")
        frame_topo.pack(fill="x", padx=16, pady=(14, 4))

        ctk.CTkLabel(
            frame_topo,
            text=f"📟  Comando Livre — Cisco IOS ({origem})",
            font=("Segoe UI", 13, "bold"),
            text_color=COR_DESTAQUE,
        ).pack(side="left")

        ctk.CTkLabel(
            modal,
            text="Digite o comando livre para o switch (ex: show mac address-table, ping, show vlan brief):",
            font=FONTE_LABEL,
            text_color=COR_CINZA,
        ).pack(anchor="w", padx=16, pady=(0, 6))

        frame_input = ctk.CTkFrame(modal, fg_color="transparent")
        frame_input.pack(fill="x", padx=16, pady=(0, 10))

        entry_cmd = ctk.CTkEntry(
            frame_input,
            placeholder_text="ex: show mac address-table",
            font=("Consolas", 11),
            fg_color="#0d1117",
            border_color=COR_FRAME_BORDA,
            height=34,
        )
        entry_cmd.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_executar = ctk.CTkButton(
            frame_input,
            text="⚡ Executar",
            fg_color=COR_BOTAO_PRIMARIO,
            hover_color=COR_BOTAO_HOVER,
            font=FONTE_LABEL_BOLD,
            width=110,
            height=34,
        )
        btn_executar.pack(side="right")

        # Área de saída textual
        ctk.CTkLabel(modal, text="Saída do Switch:", font=FONTE_LABEL_BOLD, text_color=COR_CINZA).pack(anchor="w", padx=16, pady=(4, 2))

        txt_saida = ctk.CTkTextbox(
            modal,
            font=("Consolas", 10),
            fg_color="#0d1117",
            text_color="#00ff88",
            border_color=COR_FRAME_BORDA,
            border_width=1,
            wrap="none",
        )
        txt_saida.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        txt_saida.insert("1.0", f"! Conexão ativa: {origem}\n! Digite um comando acima e clique em 'Executar' ou pressione Enter.\n")

        # Barra inferior de botões
        frame_baixo = ctk.CTkFrame(modal, fg_color="transparent")
        frame_baixo.pack(fill="x", padx=16, pady=(0, 14))

        def copiar_saida():
            conteudo = txt_saida.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(conteudo)
            messagebox.showinfo("Copiado", "Saída copiada para a área de transferência!", parent=modal)

        btn_copiar = ctk.CTkButton(
            frame_baixo,
            text="📋 Copiar Saída",
            command=copiar_saida,
            fg_color="#233549",
            hover_color="#304760",
            font=FONTE_LABEL,
            height=30,
        )
        btn_copiar.pack(side="left")

        btn_fechar = ctk.CTkButton(
            frame_baixo,
            text="Fechar",
            command=modal.destroy,
            fg_color="#3a3a3a",
            hover_color="#555",
            font=FONTE_LABEL,
            height=30,
            width=80,
        )
        btn_fechar.pack(side="right")

        # Lógica de execução em background thread
        def executar():
            cmd = entry_cmd.get().strip()
            if not cmd:
                return

            btn_executar.configure(state="disabled", text="⏳ Executando...")
            txt_saida.delete("1.0", "end")
            txt_saida.insert("1.0", f"! Executando '{cmd}' via {origem}...\n")

            def worker():
                try:
                    if origem == "SSH":
                        if not self.ssh_client or not self.ssh_client.conectado:
                            raise RuntimeError("Conexão SSH perdida.")
                        saida = self.ssh_client.executar_comando(cmd)
                    else:
                        # Modo Serial
                        if not self._serial_client or not self._serial_client.conectado or not self.serial_conn:
                            raise RuntimeError("Conexão Serial perdida.")

                        with self._serial_client._lock:
                            self.serial_conn.write(b"\r\nterminal length 0\r\n")
                            self.serial_conn.flush()
                        time.sleep(0.3)

                        self._buffer_captura_serial = ""
                        self._captura_serial_ativa = True

                        with self._serial_client._lock:
                            self.serial_conn.write(cmd.encode("ascii", errors="ignore") + b"\r\n")
                            self.serial_conn.flush()

                        prazo = time.monotonic() + 6.0
                        while time.monotonic() < prazo:
                            time.sleep(0.2)
                            buff = self._buffer_captura_serial.strip()
                            if buff and (buff.endswith("#") or buff.endswith(">")):
                                break

                        self._captura_serial_ativa = False
                        saida = self._buffer_captura_serial

                    def on_sucesso():
                        btn_executar.configure(state="normal", text="⚡ Executar")
                        txt_saida.delete("1.0", "end")
                        txt_saida.insert("1.0", saida)

                        # Atualiza também o Console de Preview no painel lateral
                        if hasattr(self, "textbox_preview_ssh") and self.textbox_preview_ssh:
                            self.textbox_preview_ssh.configure(state="normal")
                            self.textbox_preview_ssh.delete("1.0", "end")
                            cabec = f"! ======================================\n! COMANDO MANUAL EXECUTADO ({origem}): {cmd}\n! ======================================\n"
                            self.textbox_preview_ssh.insert("1.0", cabec + saida)
                            self.textbox_preview_ssh.configure(state="disabled")

                    self.after(0, on_sucesso)
                except Exception as exc:
                    def on_erro(err_msg=str(exc)):
                        btn_executar.configure(state="normal", text="⚡ Executar")
                        txt_saida.delete("1.0", "end")
                        txt_saida.insert("1.0", f"! ERRO AO EXECUTAR COMANDO:\n! {err_msg}\n")
                    self.after(0, on_erro)

            threading.Thread(target=worker, daemon=True).start()

        btn_executar.configure(command=executar)
        entry_cmd.bind("<Return>", lambda e: executar())
        entry_cmd.focus_set()



# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = AppSwitchManager()
    app.mainloop()
