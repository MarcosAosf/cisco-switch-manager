# Cisco Switch Manager (Infraestrutura & Redes) 🌐

Uma suíte desktop completa e moderna com interface gráfica em **CustomTkinter** e motores de comunicação híbridos (**SSH com Netmiko** e **Serial Console com PySerial**), desenvolvida para administração, diagnóstico, monitoramento de portas e provisionamento automatizado de switches **Cisco Catalyst e Enterprise Switches (IOS / IOS-XE)**.

---

## 🚀 Funcionalidades Principais

- **Comunicação Híbrida (SSH & Serial Console):**
  - **Acesso SSH Seguro:** Integração com Netmiko (`cisco_ios`) suportando cifras modernas e legadas com autenticação flexível e elevação `enable`.
  - **Acesso Serial Direto (Console RS-232/USB):** Detecção automática de portas COM ativas no Windows/Linux, baudrate configurável (padrão 9600) e terminal interativo com envio de Break sequences.
- **Gerenciador de Portas e Interfaces (Port Matrix):**
  - Leitura em tempo real do status das portas (`show interfaces status`).
  - Parsing de status (`connected`, `notconnect`, `disabled`, `err-disabled`), duplex, velocidade, VLAN e descrição.
  - Ações rápidas com 1 clique: **Enable (no shut)**, **Disable (shut)**, **Alterar VLAN** e **Editar Descrição**.
- **Cofre de Switches & Inventário Local (Hosts Vault):**
  - Cadastro de switches por grupos operacionais (Core, Distribuição, Acesso).
  - Indicador de status de conectividade por ICMP Ping ao vivo.
  - Carregamento rápido de credenciais e IPs sem necessidade de digitação repetitiva.
- **Provisionamento Automatizado (Day-0 Deployment):**
  - Configuração de switches zerados via Console ou SSH com templates padronizados.
  - Definição de Hostname, VLAN de gerência, SSH v2, RSA 2048-bit, Spanning-Tree (PVST/Portfast), NTP, Syslog e Banner MOTD.
- **Diagnóstico & Ferramentas Avançadas:**
  - Mapeamento de vizinhos com **CDP** (`show cdp neighbors detail`) e **LLDP**.
  - Tabela de endereços MAC (`show mac address-table`).
  - Histórico de logs de buffer (`show logging`) e status de fontes/alimentação.
  - Terminal interativo integrado com log de sessão em tempo real.

---

## 📋 Pré-requisitos & Instalação

### Requisitos:
- **Python 3.8+** instalado.
- Cabo Console Serial (USB/RS-232) ou conectividade IP/SSH com o switch.

### Instalação:
1. Clone o repositório:
```bash
git clone https://github.com/MarcosAosf/cisco-switch-manager.git
cd cisco-switch-manager
```

2. Crie e ative um ambiente virtual:
```bash
python -m venv venv
# No Windows:
venv\Scripts\activate
# No Linux/macOS:
source venv/bin/activate
```

3. Instale as dependências:
```bash
pip install -r requirements.txt
```

4. *(Opcional)* Configure seu inventário inicial copiando o arquivo de exemplo:
```bash
cp hosts.example.json hosts.json
```

---

## ⚡ Como Executar

Inicie a aplicação:
```bash
python main.py
```

- **Para conexão via SSH:** Selecione a aba SSH, insira o IP do switch (ex.: `192.0.2.10`), usuário e senha e clique em **Conectar**.
- **Para conexão via Serial Console:** Conecte o cabo console no computador, selecione a porta COM correspondente no menu suspenso e clique em **Conectar Serial**.

---

## 🔒 Segurança & Boas Práticas

- **Arquitetura de Memória Volátil:** Nenhuma senha ou credencial de acesso é salva em disco ou no arquivo de inventário. O arquivo `hosts.json` armazena exclusivamente metadados de identificação (nome, IP, usuário e grupo) e está incluído no `.gitignore`.
- **Prevenção de Vazamentos:** Nunca compartilhe arquivos de logs brutos gerados durante testes de laboratório ou credenciais de produção em repositórios públicos.

---

## 📂 Estrutura do Projeto

```text
cisco-switch-manager/
├── cisco_ssh.py          # Camada de comunicação SSH com Netmiko
├── cisco_serial.py       # Camada de comunicação serial com PySerial
├── inventory.py          # Gerenciamento de inventário local e grupos
├── hosts.example.json    # Modelo de inventário de switches de exemplo
├── templates.py          # Templates certificados de comandos Cisco IOS
├── main.py               # Interface gráfica completa (CustomTkinter)
├── requirements.txt      # Dependências do projeto
├── .gitignore            # Proteção contra versionamento de credenciais e logs
└── README.md             # Documentação técnica da ferramenta
```

---

## 📄 Licença

Distribuído sob a licença **MIT**. Veja o arquivo `LICENSE` para mais detalhes.
