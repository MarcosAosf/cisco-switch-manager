"""
cisco_ssh.py
------------
Módulo de conexão SSH para gerência contínua de switches Cisco (Day-1+).
Utiliza a biblioteca Netmiko para operações remotas seguras via IP.
"""

import re
import logging
import sys
import types
from typing import List, Dict, Any, Optional

# =========================================================================
# MAGIC POLYFILL: Enganar o Python 3.13+ que removeu o 'telnetlib' nativo.
# O Netmiko 4.2.0 (necessário para compatibilidade com o Cisco 2960) exige 
# essa biblioteca no import, mesmo se usarmos apenas SSH. 
# Criamos uma "casca" vazia dinâmica na memória para satisfazer Telnet e constantes.
# =========================================================================
class _DummyTelnetlib(types.ModuleType):
    Telnet = type("Telnet", (object,), {})

    def __getattr__(self, name: str) -> Any:
        return bytes([1])


if "telnetlib" not in sys.modules:
    dummy_telnetlib = _DummyTelnetlib("telnetlib")
    dummy_telnetlib.Telnet = type("Telnet", (object,), {})
    sys.modules["telnetlib"] = dummy_telnetlib
# =========================================================================

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoTimeoutException,
    NetmikoAuthenticationException,
    SSHException,
)

logger = logging.getLogger(__name__)


def parse_interfaces_status(raw_output: str) -> List[Dict[str, Any]]:
    """
    Parser universal com Regex e Fallback para a saída de 'show interfaces status'.
    Lida com descrições com espaços e nomes de interfaces de vários formatos.
    Pode ser usado tanto para saídas via SSH quanto via Console Serial (COM).
    """
    portas: List[Dict[str, Any]] = []

    regex_status = re.compile(
        r"^(?P<port>[A-Za-z0-9/]+)\s+(?P<name>.*?)\s*(?P<status>connected|notconnect|disabled|err-disabled|faulty|inactive)\s+(?P<vlan>[0-9]+|trunk|routed)\s+(?P<duplex>\S+)\s+(?P<speed>\S+)\s*(?P<type>.*)$"
    )

    linhas = [linha.strip() for linha in raw_output.splitlines() if linha.strip()]

    for linha in linhas:
        if linha.startswith("Port ") or linha.startswith("%") or linha.startswith("--") or "show interfaces" in linha:
            continue

        match = regex_status.match(linha)
        if match:
            dados = match.groupdict()
            vlan = dados["vlan"].strip()
            status_str = dados["status"].strip()
            modo = "Trunk" if vlan.lower() == "trunk" else "Access"
            is_active = status_str.lower() not in ("disabled", "err-disabled")
            portas.append({
                "porta": dados["port"].strip(),
                "descricao": dados["name"].strip(),
                "status": status_str,
                "ativo": is_active,
                "vlan": vlan if modo == "Access" else "all",
                "modo": modo,
                "velocidade": dados["speed"].strip(),
                "tipo": dados.get("type", "").strip(),
            })
        else:
            partes = linha.split()
            if len(partes) >= 5 and any(partes[0].startswith(p) for p in ("Fa", "Gi", "Te", "Po", "Eth")):
                porta = partes[0]
                status = partes[-5] if len(partes) >= 6 else "unknown"
                vlan = partes[-4] if len(partes) >= 6 else "1"
                modo = "Trunk" if vlan.lower() == "trunk" else "Access"
                descricao = " ".join(partes[1:-5]) if len(partes) > 6 else ""
                is_active = status.lower() not in ("disabled", "err-disabled")
                portas.append({
                    "porta": porta,
                    "descricao": descricao,
                    "status": status,
                    "ativo": is_active,
                    "vlan": vlan if modo == "Access" else "all",
                    "modo": modo,
                    "velocidade": partes[-2] if len(partes) >= 6 else "",
                    "tipo": partes[-1] if len(partes) >= 6 else "",
                })

    return portas


def normalize_interface_name(name: str) -> str:
    """
    Normaliza nomes de interfaces para formato padronizado (ex: 'Gig 0/1' -> 'Gi0/1', 'FastEthernet0/1' -> 'Fa0/1').
    """
    n = name.strip()
    n = re.sub(r"\s+", "", n)
    substituicoes = [
        (r"^FastEthernet|^Fas|^Fa", "Fa"),
        (r"^GigabitEthernet|^Gig|^Gi", "Gi"),
        (r"^TenGigabitEthernet|^Ten|^Te", "Te"),
        (r"^Ethernet|^Eth|^Et", "Et"),
        (r"^Port-channel|^Po", "Po"),
    ]
    for padrao, pref in substituicoes:
        if re.match(padrao, n, re.IGNORECASE):
            return re.sub(padrao, pref, n, flags=re.IGNORECASE)
    return n


def parse_cdp_neighbors(raw_output: str) -> Dict[str, Dict[str, str]]:
    """
    Parser com Regex para saída de 'show cdp neighbors'.
    Extrai: Device ID (Hostname do vizinho), Interface Local, Platform e Port ID.
    Lida com quebras de linha em Device IDs longos e normaliza os nomes das interfaces.
    """
    vizinhos: Dict[str, Dict[str, str]] = {}
    linhas = raw_output.splitlines()

    header_idx = -1
    for i, line in enumerate(linhas):
        if "Device ID" in line and "Local Intrfce" in line:
            header_idx = i
            break

    if header_idx == -1:
        return vizinhos

    cap_codes = set("RTBSHIrPDCM")
    pending_dev: Optional[str] = None

    for line in linhas[header_idx + 1:]:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("%") or "show cdp" in line_clean:
            continue

        tokens = line_clean.split()
        if len(tokens) == 1 and not any(tokens[0].startswith(p) for p in ("Fa", "Gi", "Te", "Eth", "Po")):
            pending_dev = tokens[0]
            continue

        if pending_dev:
            dev = pending_dev
            pending_dev = None
            rest = tokens
        else:
            dev = tokens[0]
            rest = tokens[1:]

        # Encontra o holdtime (número inteiro entre 1 e 255)
        hold_idx = -1
        for i, tok in enumerate(rest):
            if tok.isdigit() and 1 <= int(tok) <= 255:
                hold_idx = i
                break

        if hold_idx == -1:
            continue

        intf_parts = rest[:hold_idx]
        local_intf = "".join(intf_parts)
        intf_norm = normalize_interface_name(local_intf)

        after_hold = rest[hold_idx + 1:]
        plat_idx = 0
        for i, tok in enumerate(after_hold):
            if not (len(tok) == 1 and tok in cap_codes):
                plat_idx = i
                break

        # Port ID geralmente corresponde ao último ou dois últimos tokens
        if len(after_hold) >= plat_idx + 2 and any(after_hold[-2].startswith(p) for p in ("Fa", "Gi", "Te", "Eth", "Port", "Gig", "Fas")):
            port_id = " ".join(after_hold[-2:])
            plat_tokens = after_hold[plat_idx:-2]
        else:
            port_id = after_hold[-1] if after_hold else ""
            plat_tokens = after_hold[plat_idx:-1] if after_hold else []

        platform = " ".join(plat_tokens) if plat_tokens else "Cisco Device"

        info = {
            "device_id": dev,
            "local_interface": local_intf,
            "platform": platform,
            "port_id": port_id,
        }
        vizinhos[intf_norm] = info
        vizinhos[local_intf] = info
        vizinhos[local_intf.lower()] = info

    return vizinhos


def parse_mac_address_table(raw_output: str) -> Dict[str, List[str]]:
    """
    Parser com Regex para saída de 'show mac address-table' / 'show mac-address-table'.
    Extrai: VLAN, MAC Address, Type e Port.
    Retorna dicionário mapeando nome da interface para lista de MAC addresses associados.
    """
    macs_por_porta: Dict[str, List[str]] = {}
    mac_re = re.compile(r"([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})")
    intf_re = re.compile(
        r"((?:Fa|Gi|Te|Po|Eth|FastEthernet|GigabitEthernet|TenGigabitEthernet|Port-channel)\s*\d+(?:/\d+)*(?:\.\d+)?)",
        re.IGNORECASE,
    )

    linhas = raw_output.splitlines()
    for linha in linhas:
        l_str = linha.strip()
        if not l_str or l_str.startswith("--") or "Total Mac Addresses" in l_str or "Mac Address Table" in l_str or "vlan" in l_str.lower() and "mac" in l_str.lower():
            continue

        m_mac = mac_re.search(l_str)
        m_intf = intf_re.search(l_str)
        if m_mac and m_intf:
            mac = m_mac.group(1).lower()
            port_raw = m_intf.group(1).strip()
            port_norm = normalize_interface_name(port_raw)

            if port_norm not in macs_por_porta:
                macs_por_porta[port_norm] = []
            if mac not in macs_por_porta[port_norm]:
                macs_por_porta[port_norm].append(mac)

            if port_raw not in macs_por_porta:
                macs_por_porta[port_raw] = []
            if mac not in macs_por_porta[port_raw]:
                macs_por_porta[port_raw].append(mac)

    return macs_por_porta


def extrair_dns_cme(dn_output: str) -> Dict[str, Dict[str, str]]:
    """
    Extrai Directory Numbers (DN) do Cisco CME dividindo por blocos:
    - SIP: 'voice register dn <ID>'
    - SCCP: 'ephone-dn <ID>'

    Retorna um dicionário mapeando dn_id -> {'dn_id': ..., 'ramal': ..., 'nome': ...}.
    """
    dict_dns: Dict[str, Dict[str, str]] = {}
    if not dn_output:
        return dict_dns

    # 1. Divisão por blocos (voice register dn <ID> ou ephone-dn <ID>)
    parts_dn = re.split(r"(?im)^\s*(?:(?:voice\s+register\s+)?dn|ephone-dn)\s+(\d+)\b[^\r\n]*", dn_output)
    if len(parts_dn) > 1:
        for i in range(1, len(parts_dn), 2):
            dn_id = parts_dn[i].strip()
            body = parts_dn[i + 1]
            m_num = re.search(r"\b(?:number|num)\s+(\d+)", body, re.IGNORECASE)
            m_name = re.search(r"\b(?:name|description)\s+([^\r\n]+)", body, re.IGNORECASE)
            num = m_num.group(1).strip() if m_num else "-"
            raw_name = m_name.group(1).strip() if m_name else "-"
            clean_name = re.sub(r"^(?:primary\s+name|name)[:\s]*", "", raw_name, flags=re.IGNORECASE).strip(' \t\r\n"\'')
            dict_dns[dn_id] = {
                "dn_id": dn_id,
                "ramal": num,
                "nome": clean_name if clean_name else (f"Ramal {num}" if num != "-" else "-"),
            }

    # 2. Fallback / Complemento: linhas tabulares (ex: saídas simplificadas)
    for line in dn_output.splitlines():
        line_c = line.strip()
        if not line_c or line_c.startswith(("-", "=", "#")) or "% Invalid" in line_c:
            continue
        if re.match(r"^(?:DN|TAG|EPHONE-DN)\s+[A-Za-z#_-]", line_c, re.IGNORECASE):
            continue
        m = re.match(
            r"^\s*(?:(?:voice\s+register\s+)?dn\s+|ephone-dn\s+)?(\d+)(?:\s+(?:tag\s+)?\d+)?(?:\s+|\s*\(\s*)(\d{2,8})\)?\s*(.*?)(?:\s+(IDLE|BUSY|REGISTERED|UNREGISTERED|ACTIVE|OFFLINE))?$",
            line_c,
            re.IGNORECASE,
        )
        if m:
            dn_id = m.group(1).strip()
            if dn_id not in dict_dns or dict_dns[dn_id].get("ramal") == "-":
                num = m.group(2).strip()
                raw_name = (m.group(3) or "").strip()
                clean_name = re.sub(r"^(?:primary\s+name|name)[:\s]*", "", raw_name, flags=re.IGNORECASE).strip(' \t\r\n"\'')
                dict_dns[dn_id] = {
                    "dn_id": dn_id,
                    "ramal": num,
                    "nome": clean_name if clean_name else f"Ramal {num}",
                }

    return dict_dns


def parse_cme_sip_status(
    dn_output: str,
    pool_output: str,
    pool_status_output: str = "",
) -> List[Dict[str, Any]]:
    """
    Parser universal com Leitura por Blocos e Cruzamento Relacional (Join):
    1. Mapeia todos os DNs (Directory Numbers) em dict_dns.
    2. Lê os Pools em blocos individuais, capturando ID, MAC e associação 'number 1 dn <ID>' (ou 'button 1:<ID>').
    3. Cruza IP e Status a partir de 'show voice register pool brief' / 'summary'.
    4. Realiza o Join garantindo que ramal, nome, ip e dn_id sejam devidamente preenchidos.
    """
    # 1. Mapear DNs
    dict_dns = extrair_dns_cme(dn_output)

    # 2. Mapa de IP e Status a partir de show voice register pool brief / summary
    status_map: Dict[str, Dict[str, str]] = {}
    texto_status = f"{pool_output}\n{pool_status_output}"

    for line in texto_status.splitlines():
        line_c = line.strip()
        if not line_c or line_c.startswith(("-", "=", "#")) or "% Invalid" in line_c:
            continue
        if re.match(r"^(?:POOL|DEVICE|EPHONE|MAC)\s+[A-Za-z#_-]", line_c, re.IGNORECASE):
            continue

        pm = re.match(r"^\s*(?:(?:voice\s+register\s+)?pool\s+|ephone[-_\s]*)?(\d+)", line_c, re.IGNORECASE)
        ip_m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line_c)
        mac_m = re.search(
            r"(?:SEP|Mac:)?([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}|[0-9a-fA-F]{12})",
            line_c,
            re.IGNORECASE,
        )
        st_m = re.search(r"\b(REGISTERED|UNREGISTERED|OFFLINE|ONLINE|DE-REGISTERED|ACTIVE)\b", line_c, re.IGNORECASE)

        ip_val = ip_m.group(1).strip() if ip_m else "-"
        raw_mac = mac_m.group(1).strip() if mac_m else "-"
        st_val = st_m.group(1).strip() if st_m else "Unregistered"

        if pm:
            p_id = pm.group(1).strip()
            if p_id not in status_map:
                status_map[p_id] = {"ip": ip_val, "mac": raw_mac, "status": st_val}
            else:
                if ip_val != "-":
                    status_map[p_id]["ip"] = ip_val
                if raw_mac != "-":
                    status_map[p_id]["mac"] = raw_mac
                if st_val != "Unregistered":
                    status_map[p_id]["status"] = st_val

        if raw_mac != "-":
            c_mac = re.sub(r"[^0-9a-fA-F]", "", raw_mac).lower()
            if len(c_mac) == 12:
                norm_mac = f"{c_mac[0:4]}.{c_mac[4:8]}.{c_mac[8:12]}"
                status_map[norm_mac] = {"ip": ip_val, "status": st_val}

    # 3. Ler Pools em Blocos
    pools_encontrados: Dict[str, Dict[str, Any]] = {}
    parts_pool = re.split(r"(?im)^\s*(?:(?:voice\s+register\s+)?pool|ephone)\s+(\d+)\b[^\r\n]*", pool_output)

    if len(parts_pool) > 1:
        for i in range(1, len(parts_pool), 2):
            pool_id = parts_pool[i].strip()
            body = parts_pool[i + 1]

            m_mac = re.search(r"(?:id\s+mac|mac-address|mac:?)\s*([0-9a-fA-F\.\-]{12,17})", body, re.IGNORECASE)
            raw_mac = m_mac.group(1).strip() if m_mac else "-"

            m_ip = re.search(r"\b(?:ip(?:\s+address)?|ip:)\s*(\d{1,3}(?:\.\d{1,3}){3})", body, re.IGNORECASE)
            ip_val = m_ip.group(1).strip() if m_ip else "-"

            m_st = re.search(r"\b(REGISTERED|UNREGISTERED|OFFLINE|ONLINE|DE-REGISTERED|ACTIVE)\b", body, re.IGNORECASE)
            st_val = m_st.group(1).strip() if m_st else "Unregistered"

            # Associação de DN: SIP 'number 1 dn <ID>' e SCCP 'button 1:<ID>'
            m_dn = re.search(r"\b(?:number\s+\d+\s+dn|button\s+\d+\s*:\s*|dn\s+)(\d+)", body, re.IGNORECASE)
            dn_id_vinculado = m_dn.group(1).strip() if m_dn else "-"

            pools_encontrados[pool_id] = {
                "pool_id": pool_id,
                "mac": raw_mac,
                "ip": ip_val,
                "status": st_val,
                "dn_id": dn_id_vinculado,
            }

    # Complementa pools presentes no status_map
    for p_id, st_info in status_map.items():
        if p_id.isdigit() and p_id not in pools_encontrados:
            pools_encontrados[p_id] = {
                "pool_id": p_id,
                "mac": st_info.get("mac", "-"),
                "ip": st_info.get("ip", "-"),
                "status": st_info.get("status", "Unregistered"),
                "dn_id": "-",
            }

    # 4. Cruzamento e montagem final da lista de ramais
    resultado: List[Dict[str, Any]] = []
    sorted_pools = sorted(
        pools_encontrados.values(),
        key=lambda x: int(x["pool_id"]) if str(x["pool_id"]).isdigit() else str(x["pool_id"]),
    )

    for p in sorted_pools:
        p_id = p["pool_id"]
        dn_id = p.get("dn_id", "-")

        # MAC Address
        raw_mac = p["mac"]
        if raw_mac == "-" and p_id in status_map:
            raw_mac = status_map[p_id].get("mac", "-")

        clean_mac = re.sub(r"[^0-9a-fA-F]", "", raw_mac).lower()
        formatted_mac = f"{clean_mac[0:4]}.{clean_mac[4:8]}.{clean_mac[8:12]}" if len(clean_mac) == 12 else raw_mac

        # IP e Status
        ip_val = p["ip"]
        st_val = p["status"]

        if (ip_val == "-" or st_val == "Unregistered") and p_id in status_map:
            if ip_val == "-":
                ip_val = status_map[p_id].get("ip", "-")
            if st_val == "Unregistered":
                st_val = status_map[p_id].get("status", "Unregistered")

        if (ip_val == "-" or st_val == "Unregistered") and formatted_mac != "-" and formatted_mac in status_map:
            if ip_val == "-":
                ip_val = status_map[formatted_mac].get("ip", "-")
            if st_val == "Unregistered":
                st_val = status_map[formatted_mac].get("status", "Unregistered")

        st_upper = st_val.upper()
        status_final = "Registrado" if st_upper in ("REGISTERED", "ONLINE", "ACTIVE") else "Offline"

        # Cruzamento com dict_dns usando dn_id_vinculado
        dn_info = {}
        if dn_id != "-" and dn_id in dict_dns:
            dn_info = dict_dns[dn_id]
        elif p_id in dict_dns:
            dn_info = dict_dns[p_id]
            if dn_id == "-":
                dn_id = p_id

        ramal_val = dn_info.get("ramal", "-")
        nome_val = dn_info.get("nome", "-")

        resultado.append({
            "pool": f"Pool {p_id}",
            "pool_id": p_id,
            "dn_id": dn_id if dn_id != "-" else "Sem DN",
            "mac": formatted_mac,
            "mac_address": formatted_mac,
            "ip": ip_val,
            "status": status_final,
            "ramal": ramal_val,
            "nome": nome_val,
        })

    # Caso não haja pools mas haja DNs cadastrados, lista os DNs
    if not resultado and dict_dns:
        for d_id, dn in sorted(dict_dns.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else str(x[0])):
            resultado.append({
                "pool": "-",
                "pool_id": "-",
                "dn_id": d_id,
                "mac": "-",
                "mac_address": "-",
                "ip": "-",
                "status": "Offline",
                "ramal": dn.get("ramal", "-"),
                "nome": dn.get("nome", "-"),
            })

    return resultado


class CiscoSSHClient:
    """Cliente SSH baseado em Netmiko para gerência remota de switches Cisco."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        secret: str = "",
        port: int = 22,
        timeout: int = 15,
    ) -> None:
        self.host = host.strip()
        self.username = username.strip()
        self.password = password
        self.secret = secret or password
        self.port = port
        self.timeout = timeout
        self.connection = None
        self.conectado = False

    def conectar(self) -> None:
        """Estabelece a conexão SSH com o switch Cisco."""
        device_params = {
            "device_type": "cisco_ios",
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "secret": self.secret,
            "port": self.port,
            "conn_timeout": self.timeout,
            "fast_cli": False,
        }
        try:
            self.connection = ConnectHandler(**device_params)

            try:
                if not self.connection.check_enable_mode():
                    self.connection.enable()
            except Exception:
                pass

            self.conectado = True
            logger.info("Conectado com sucesso via SSH a %s", self.host)
        except (NetmikoAuthenticationException, NetmikoTimeoutException, SSHException) as exc:
            self.conectado = False
            self.connection = None
            logger.error("Erro de conexão SSH: %s", exc)
            raise
        except Exception as exc:
            self.conectado = False
            self.connection = None
            logger.error("Erro inesperado na conexão SSH: %s", exc)
            raise

    def desconectar(self) -> None:
        """Encerra a sessão SSH de forma segura."""
        if self.connection:
            try:
                self.connection.disconnect()
            except Exception:
                pass
            finally:
                self.connection = None
                self.conectado = False

    def executar_comando(self, comando: str) -> str:
        """Executa um comando livre no switch via SSH e retorna a saída textual."""
        if not self.conectado or not self.connection:
            raise RuntimeError("Não há conexão SSH ativa com o switch.")
        return self.connection.send_command(comando)

    def obter_status_portas(self) -> List[Dict[str, Any]]:
        """
        Executa 'show interfaces status' no switch e retorna uma lista estruturada
        de portas contendo: porta, descricao, status, vlan, modo, velocidade.
        """
        if not self.conectado or not self.connection:
            raise RuntimeError("Não há conexão SSH ativa com o switch.")

        raw_output = self.connection.send_command("show interfaces status")
        return parse_interfaces_status(raw_output)

    def _fazer_parse_interfaces_status(self, raw_output: str) -> List[Dict[str, Any]]:
        return parse_interfaces_status(raw_output)

    def obter_vizinhos_cdp(self) -> Dict[str, Dict[str, str]]:
        """
        Executa 'show cdp neighbors' no switch e retorna um dicionário mapeado
        por interface local com Device ID, Platform e Port ID do vizinho.
        """
        if not self.conectado or not self.connection:
            raise RuntimeError("Não há conexão SSH ativa com o switch.")

        raw_output = self.connection.send_command("show cdp neighbors")
        return parse_cdp_neighbors(raw_output)

    def obter_mac_addresses(self) -> Dict[str, List[str]]:
        """
        Executa 'show mac address-table' (com fallback para 'show mac-address-table')
        e retorna um dicionário mapeando nome da interface para a lista de endereços MAC.
        """
        if not self.conectado or not self.connection:
            raise RuntimeError("Não há conexão SSH ativa com o switch.")

        try:
            raw_output = self.connection.send_command("show mac address-table")
            if "% Invalid input" in raw_output or "% Incomplete" in raw_output:
                raw_output = self.connection.send_command("show mac-address-table")
        except Exception:
            raw_output = self.connection.send_command("show mac-address-table")

        return parse_mac_address_table(raw_output)

    def obter_running_config_interface(self, interface: str) -> str:
        """
        Executa 'show running-config interface <interface>' para capturar o
        snapshot de configuração atual da porta antes de aplicar alterações.
        """
        if not self.conectado or not self.connection:
            raise RuntimeError("Não há conexão SSH ativa com o switch.")

        return self.connection.send_command(f"show running-config interface {interface}")

    def obter_snapshot_interfaces(self, interfaces: List[str]) -> Dict[str, str]:
        """
        Captura a running-config de uma lista de interfaces para geração de rollback.
        """
        snapshot: Dict[str, str] = {}
        for iface in interfaces:
            try:
                cfg = self.obter_running_config_interface(iface)
                snapshot[iface] = cfg
            except Exception as e:
                logger.warning("Falha ao capturar snapshot da interface %s: %s", iface, e)
                snapshot[iface] = f"! Erro ao obter running-config: {e}"
        return snapshot

    def enviar_configuracoes(self, comandos: List[str]) -> str:
        """
        Envia uma lista de comandos de configuração para o switch via SSH
        e salva a configuração na NVRAM.
        """
        if not self.conectado or not self.connection:
            raise RuntimeError("Não há conexão SSH ativa com o switch.")

        if not comandos:
            return "Nenhum comando a aplicar."

        resultado = self.connection.send_config_set(comandos)
        try:
            self.connection.save_config()
        except Exception:
            pass
        return resultado

    def obter_status_cme_sip(self) -> List[Dict[str, Any]]:
        """
        Executa comandos de diagnóstico no roteador Cisco CME para extrair os ramais.
        Suporta de forma transparente tanto o protocolo SIP (voice register dn / pool)
        quanto o protocolo SCCP/Skinny (ephone-dn / ephone).

        Desativa temporariamente a paginação ('terminal length 0') para despejo contínuo
        de centenas de ramais sem interrupção pelo prompt '--More--'.
        """
        if not self.conectado or not self.connection:
            raise RuntimeError("Não há conexão SSH ativa com o roteador CME.")

        # 1. Desativa a paginação para leitura ininterrupta de 100+ registros
        try:
            self.connection.send_command("terminal length 0")
        except Exception as e:
            logger.warning("Falha ao configurar terminal length 0 no CME: %s", e)

        try:
            # 1. Tenta comandos SIP (voice register dn / pool)
            dn_output = ""
            pool_output = ""
            pool_status_output = ""

            # 1.1 Extração de DNs (show voice register dn)
            try:
                dn_output = self.connection.send_command("show voice register dn", read_timeout=90)
                if "% Invalid" in dn_output or not dn_output.strip():
                    dn_output = self.connection.send_command("show voice register dn brief", read_timeout=90)
                    if "% Invalid" in dn_output or not dn_output.strip():
                        dn_output = self.connection.send_command("show voice register dn summary", read_timeout=90)
            except Exception as e:
                logger.warning("Falha ao executar comandos voice register dn: %s", e)

            # 1.2 Extração de Pools (detalhes completos com mapeamento dn)
            try:
                pool_output = self.connection.send_command("show voice register pool", read_timeout=90)
                if "% Invalid" in pool_output or not pool_output.strip():
                    pool_output = self.connection.send_command("show voice register all", read_timeout=90)
            except Exception as e:
                logger.warning("Falha ao executar comandos voice register pool detalhados: %s", e)

            # 1.3 Extração de Status em tempo real e IP (show voice register pool brief / summary)
            try:
                pool_status_output = self.connection.send_command("show voice register pool brief", read_timeout=90)
                if "% Invalid" in pool_status_output or not pool_status_output.strip():
                    pool_status_output = self.connection.send_command("show voice register pool summary", read_timeout=90)
            except Exception as e:
                logger.warning("Falha ao executar comandos voice register pool status: %s", e)

            itens_sip = parse_cme_sip_status(dn_output, pool_output, pool_status_output)
            if itens_sip:
                logger.info("CME: %d ramal(is) SIP identificado(s).", len(itens_sip))
                return itens_sip

            # 2. Se SIP não retornou dados, tenta comandos SCCP/Skinny (ephone-dn / ephone)
            ephone_dn = ""
            ephone_out = ""
            ephone_status = ""

            try:
                ephone_dn = self.connection.send_command("show ephone-dn", read_timeout=90)
                if "% Invalid" in ephone_dn or not ephone_dn.strip():
                    ephone_dn = self.connection.send_command("show ephone-dn brief", read_timeout=90)
            except Exception:
                ephone_dn = ""

            try:
                ephone_out = self.connection.send_command("show ephone", read_timeout=90)
            except Exception:
                ephone_out = ""

            try:
                ephone_status = self.connection.send_command("show ephone summary", read_timeout=90)
                if "% Invalid" in ephone_status or not ephone_status.strip():
                    ephone_status = self.connection.send_command("show ephone brief", read_timeout=90)
            except Exception:
                ephone_status = ""

            itens_sccp = parse_cme_sip_status(ephone_dn, ephone_out, ephone_status)
            if itens_sccp:
                logger.info("CME: %d ramal(is) SCCP identificado(s).", len(itens_sccp))
                return itens_sccp

            return []
        finally:
            # Restaura a paginação padrão no Cisco IOS
            try:
                self.connection.send_command("terminal length 24")
            except Exception:
                pass
