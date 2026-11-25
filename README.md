# Cisco Switch Manager (Infraestrutura & Redes) 🌐

[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-blue.svg?logo=windows)](https://microsoft.com/windows)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg?logo=python)](https://python.org)
[![UI: CustomTkinter](https://img.shields.io/badge/UI-CustomTkinter%20Modern%20Dark-A855F7.svg)](https://github.com/TomSchimansky/CustomTkinter)
[![License: MIT](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)

Uma suíte desktop nativa para **Windows (10 / 11)** com interface gráfica moderna em **CustomTkinter** e motores de comunicação híbridos (**SSH com Netmiko** e **Serial Console COM com PySerial**), desenvolvida para administração, diagnóstico, monitoramento de portas e provisionamento automatizado de switches **Cisco Catalyst e Enterprise Switches (IOS / IOS-XE)**.

---

## 🪟 Desenvolvido para Windows

O projeto foi projetado especificamente para técnicos e analistas de infraestrutura que operam em ambiente **Windows**:
- **Suporte Nativo a Portas COM:** Detecção automática de cabos conversores USB-Serial (FTDI, Prolific, CH340) nas portas `COM1`, `COM2`, `COM3`, etc., via `pyserial`.
- **Interface Gráfica Windows Dark Mode:** Janelas fluidas, fontes legíveis e suporte a renderização de alto DPI no Windows via `CustomTkinter`.
- **Launcher de Execução Direta (`run.bat`):** Permite iniciar a ferramenta com um simples **duplo clique**, sem precisar abrir o terminal.
- **Auditoria de Conectividade Windows:** Monitoramento contínuo de ping usando chamadas ICMP nativas do Windows (`ping -n 1 -w 1000`).

---

## 🚀 Funcionalidades Principais

- **Comunicação Híbrida (SSH & Serial Console):**
  - **Acesso SSH Seguro:** Integração com Netmiko (`cisco_ios`) suportando cifras modernas e legadas com autenticação flexível e elevação `enable`.
  - **Acesso Serial Direto (Console USB/RS-232):** Conexão direta na porta física de console do switch para recuperação de desastres ou primeiro provisionamento (Day-0).
- **Gerenciador de Portas e Interfaces (Port Matrix):**
  - Leitura em tempo real do status das portas (`show interfaces status`).
  - Parsing detalhado de status (`connected`, `notconnect`, `disabled`, `err-disabled`), duplex, velocidade, VLAN e descrição.
  - Ações rápidas com 1 clique: **Enable (no shut)**, **Disable (shut)**, **Alterar VLAN** e **Editar Descrição**.
- **Cofre de Switches & Inventário Local (Hosts Vault):**
  - Cadastro de switches organizados por grupos operacionais (Core, Distribuição, Acesso).
  - Indicador de status de conectividade por ICMP Ping ao vivo.
  - Carregamento rápido de IPs e parâmetros sem digitação repetitiva.
- **Provisionamento Automatizado (Day-0 Deployment):**
  - Configuração de switches zerados via Console ou SSH com templates certificados.
  - Definição de Hostname, VLAN de gerência, SSH v2, RSA 2048-bit, Spanning-Tree (PVST/Portfast), NTP, Syslog e Banner MOTD.
- **Diagnóstico & Ferramentas Avançadas:**
  - Mapeamento de vizinhos com **CDP** (`show cdp neighbors detail`) e **LLDP**.
  - Tabela de endereços MAC (`show mac address-table`).
  - Histórico de logs de buffer (`show logging`) e status de fontes/alimentação.
  - Terminal interativo integrado com log de sessão em tempo real.

---

## ⚡ Instalação e Execução no Windows (Rápido)

### 1. Pré-requisitos
- Ter o **Python 3.8+** instalado no Windows (baixe em [python.org](https://www.python.org/downloads/)).
  > ⚠️ Durante a instalação do Python, marque obrigatoriamente a opção **"Add Python to PATH"**.
- Se for usar conexão serial, conecte seu cabo conversor **USB-Serial Console** no computador.

### 2. Clonar ou Baixar o Projeto
```cmd
git clone https://github.com/MarcosAosf/cisco-switch-manager.git
cd cisco-switch-manager
```

### 3. Instalar as Dependências
Abra o Prompt de Comando (CMD) ou PowerShell na pasta do projeto e execute:
```cmd
pip install -r requirements.txt
```

### 4. Configurar Inventário Inicial (Opcional)
Para começar com um modelo de inventário pronto, basta copiar o arquivo de exemplo:
```cmd
copy hosts.example.json hosts.json
```

### 5. Executar
Você pode rodar de duas formas no Windows:
- **Opção A (Mais fácil):** Dê um **duplo clique** no arquivo **`run.bat`**.
- **Opção B (Via CMD / PowerShell):**
  ```cmd
  py main.py
  :: ou
  python main.py
  ```

---

## 💻 Como Usar

### Conexão via SSH (Rede):
1. Selecione a aba **SSH**.
2. Informe o **IP do Switch** (ex.: `192.0.2.10`), Usuário, Senha e Senha de Enable (se houver).
3. Clique em **Conectar**.

### Conexão via Cabo Console (Serial):
1. Selecione a aba **Serial Console**.
2. Escolha a porta COM identificada pelo Windows (ex.: `COM3`) e a velocidade (padrão: `9600`).
3. Clique em **Conectar Serial** para abrir a sessão direta.

---

## 🔒 Segurança & Boas Práticas

- **Arquitetura de Memória Volátil:** Nenhuma senha ou credencial de acesso é salva em disco ou no arquivo de inventário. O arquivo `hosts.json` armazena exclusivamente metadados de identificação (nome, IP, usuário e grupo) e está incluído no `.gitignore`.
- **Prevenção de Vazamentos:** Nunca compartilhe arquivos de logs brutos gerados durante testes de laboratório ou credenciais de produção em repositórios públicos.

---

## 📂 Estrutura do Projeto

```text
cisco-switch-manager/
├── run.bat               # Launcher rápido para Windows (duplo clique)
├── cisco_ssh.py          # Camada de comunicação SSH com Netmiko
├── cisco_serial.py       # Camada de comunicação serial com PySerial (portas COM)
├── inventory.py          # Gerenciamento de inventário local e grupos
├── hosts.example.json    # Modelo de inventário de switches de exemplo
├── templates.py          # Templates certificados de comandos Cisco IOS
├── main.py               # Interface gráfica completa em CustomTkinter
├── requirements.txt      # Dependências do projeto
├── .gitignore            # Proteção contra versionamento de credenciais e logs
└── README.md             # Documentação técnica da ferramenta
```

---

## 📄 Licença

Distribuído sob a licença **MIT**. Veja o arquivo `LICENSE` para mais detalhes.
