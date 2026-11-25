"""
templates.py
------------
Módulo de templates de comandos Cisco IOS para provisionamento Day-0 e Day-1.
Não possui dependências de GUI, retornando listas puras de comandos formatadas.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Union


def group_interfaces(interfaces: List[str]) -> List[str]:
    """
    Agrupa interfaces consecutivas em sintaxe nativa 'interface range' do Cisco IOS.
    Exemplo: ['Fa0/1', 'Fa0/2', 'Fa0/3', 'Fa0/5', 'Gi0/1'] -> ['Fa0/1 - 3, Fa0/5, Gi0/1']
    Divide em chunks de até 5 sub-faixas para respeitar o limite de comandos do Cisco IOS.
    """
    if not interfaces:
        return []

    parsed = []
    for iface in interfaces:
        m = re.match(r"^(.*?)(\d+)$", iface.strip())
        if m:
            prefix = m.group(1)
            num = int(m.group(2))
            parsed.append((prefix, num, iface.strip()))
        else:
            parsed.append((iface.strip(), -1, iface.strip()))

    parsed.sort(key=lambda x: (x[0], x[1]))

    ranges: List[str] = []
    current_prefix: Optional[str] = None
    start_num: Optional[int] = None
    prev_num: Optional[int] = None

    for prefix, num, orig in parsed:
        if num == -1:
            if current_prefix is not None and start_num is not None and prev_num is not None:
                if start_num == prev_num:
                    ranges.append(f"{current_prefix}{start_num}")
                else:
                    ranges.append(f"{current_prefix}{start_num} - {prev_num}")
                current_prefix = None
            ranges.append(orig)
            continue

        if current_prefix is None:
            current_prefix = prefix
            start_num = num
            prev_num = num
        elif prefix == current_prefix and prev_num is not None and num == prev_num + 1:
            prev_num = num
        else:
            if start_num is not None and prev_num is not None:
                if start_num == prev_num:
                    ranges.append(f"{current_prefix}{start_num}")
                else:
                    ranges.append(f"{current_prefix}{start_num} - {prev_num}")
            current_prefix = prefix
            start_num = num
            prev_num = num

    if current_prefix is not None and start_num is not None and prev_num is not None:
        if start_num == prev_num:
            ranges.append(f"{current_prefix}{start_num}")
        else:
            ranges.append(f"{current_prefix}{start_num} - {prev_num}")

    chunks: List[str] = []
    chunk_size = 5
    for i in range(0, len(ranges), chunk_size):
        chunks.append(", ".join(ranges[i : i + chunk_size]))
    return chunks


# ---------------------------------------------------------------------------
# Classe Principal de Templates (Day-0 e Day-1)
# ---------------------------------------------------------------------------

class CiscoTemplates:
    """Templates padronizados e certificados para switches Cisco."""

    @staticmethod
    def get_standard_day0_config(
        hostname: str,
        vlan_id: int | str,
        ip: str,
        mascara: str,
        gateway: str,
    ) -> List[str]:
        """
        Retorna a lista de comandos Day-0 padronizados para
        provisionamento de switch zerado.
        """
        return [
            "enable",
            "configure terminal",

            # 1. Identificação e Domínio
            f"hostname {hostname}",
            "ip domain-name empresa.lan",

            # 2. Spanning-Tree
            "spanning-tree mode pvst",
            "spanning-tree extend system-id",

            # 3. Acesso e Segurança (SSH e Senhas globais)
            "ip ssh version 2",
            "ip ssh dh min size 2048",
            "ip ssh server algorithm encryption aes128-ctr aes192-ctr aes256-ctr",
            "ip ssh server algorithm mac hmac-sha1 hmac-sha2-256 hmac-sha2-512",
            "crypto key generate rsa modulus 2048",
            "enable secret 9 $9$dummySecretExampleString123456",

            # 4. Usuários Administrativos Padrão
            "username admin privilege 15 secret 9 $9$dummyAdminSecretString654321",

            # 5. Interface de Gerência
            f"interface vlan {vlan_id}",
            f"ip address {ip} {mascara}",
            "no shutdown",
            "exit",
            f"ip default-gateway {gateway}",

            # Configuração da Porta Trunk Padrão (Uplink)
            "interface GigabitEthernet0/1",
            "switchport trunk allowed vlan 10,20,30,99",
            "switchport mode trunk",
            "spanning-tree portfast trunk",

            # 6. Serviços Globais (HTTP, Syslog, SNMP, NTP)
            "no ip http server",
            "ip http secure-server",
            "logging host 192.0.2.50",
            "snmp-server community public-ro RO",
            "ntp server 192.0.2.100",

            # 7. Banner MOTD
            "banner motd %",
            "#=============================================================================#",
            "|                          AVISO DE ACESSO RESTRITO                           |",
            "|      Este dispositivo e destinado exclusivamente a usuarios autorizados.    |",
            "|     Todas as atividades sao registradas e monitoradas para fins de auditoria.|",
            "#=============================================================================#",
            "%",

            # 8. Configuração das Linhas de Acesso (Login Local)
            "line con 0",
            "login local",
            "line vty 0 4",
            "login local",
            "line vty 5 15",
            "login local",
            "exit",

            # 9. Finalização
            "end",
            "write memory",
        ]

    @staticmethod
    def get_access_port_config(
        interface: str,
        vlan_dados: int | str,
        vlan_voz: Optional[int | str] = None,
        description: str = "",
        is_active: bool = True,
        poe_mode: Optional[str] = None,
    ) -> List[str]:
        """
        Retorna comandos Day-1 para configuração de porta de acesso (Data + Voice opcional).
        Reseta a porta com default interface, define descrição, modo de acesso,
        spanning-tree portfast, gestão de PoE e aplica o estado administrativo (no shutdown / shutdown).
        """
        cmds = [
            f"default interface {interface}",
            f"interface {interface}",
        ]
        if description:
            cmds.append(f"description {description}")
        cmds.extend([
            "switchport mode access",
            f"switchport access vlan {vlan_dados}",
        ])
        if vlan_voz:
            cmds.append(f"switchport voice vlan {vlan_voz}")
        cmds.append("spanning-tree portfast")
        if poe_mode:
            p_str = str(poe_mode).lower()
            if "never" in p_str:
                cmds.append("power inline never")
            elif "auto" in p_str:
                cmds.append("power inline auto")
        cmds.append("no shutdown" if is_active else "shutdown")
        cmds.append("exit")
        return cmds

    @staticmethod
    def get_trunk_port_config(
        interface: str,
        allowed_vlans: str = "all",
        description: str = "Uplink",
        is_active: bool = True,
        poe_mode: Optional[str] = None,
    ) -> List[str]:
        """
        Retorna comandos Day-1 para configuração de porta Trunk/Uplink.
        Configura encapsulation dot1q, mode trunk, nonegotiate, VLANs permitidas,
        gestão de PoE e aplica o estado administrativo (no shutdown / shutdown).
        """
        cmds = [
            f"interface {interface}",
        ]
        if description:
            cmds.append(f"description {description}")
        cmds.extend([
            "switchport trunk encapsulation dot1q",
            "switchport mode trunk",
            "switchport nonegotiate",
            f"switchport trunk allowed vlan {allowed_vlans}",
        ])
        if poe_mode:
            p_str = str(poe_mode).lower()
            if "never" in p_str:
                cmds.append("power inline never")
            elif "auto" in p_str:
                cmds.append("power inline auto")
        cmds.append("no shutdown" if is_active else "shutdown")
        cmds.append("exit")
        return cmds

    @staticmethod
    def format_interface_range(interfaces: List[str]) -> List[str]:
        """Agrupa uma lista de interfaces na sintaxe 'interface range' do Cisco IOS."""
        return group_interfaces(interfaces)

    @staticmethod
    def get_bulk_access_config(
        interfaces: List[str],
        vlan_dados: int | str,
        vlan_voz: Optional[int | str] = None,
        description: str = "",
        is_active: bool = True,
        poe_mode: Optional[str] = None,
    ) -> List[str]:
        """
        Retorna comandos em lote para configuração de múltiplas portas de acesso
        utilizando a sintaxe nativa 'interface range' do Cisco IOS, incluindo estado e PoE.
        """
        if not interfaces:
            return []

        chunks = group_interfaces(interfaces)
        cmds: List[str] = []
        for range_clause in chunks:
            cmds.append(f"interface range {range_clause}")
            if description:
                cmds.append(f"description {description}")
            cmds.extend([
                "switchport mode access",
                f"switchport access vlan {vlan_dados}",
            ])
            if vlan_voz:
                cmds.append(f"switchport voice vlan {vlan_voz}")
            cmds.append("spanning-tree portfast")
            if poe_mode:
                p_str = str(poe_mode).lower()
                if "never" in p_str:
                    cmds.append("power inline never")
                elif "auto" in p_str:
                    cmds.append("power inline auto")
            cmds.append("no shutdown" if is_active else "shutdown")
            cmds.append("exit")
        return cmds

    @staticmethod
    def get_bulk_trunk_config(
        interfaces: List[str],
        allowed_vlans: str = "all",
        description: str = "",
        is_active: bool = True,
        poe_mode: Optional[str] = None,
    ) -> List[str]:
        """
        Retorna comandos em lote para configuração de múltiplas portas Trunk
        utilizando a sintaxe nativa 'interface range' do Cisco IOS, incluindo estado e PoE.
        """
        if not interfaces:
            return []

        chunks = group_interfaces(interfaces)
        cmds: List[str] = []
        for range_clause in chunks:
            cmds.append(f"interface range {range_clause}")
            if description:
                cmds.append(f"description {description}")
            cmds.extend([
                "switchport trunk encapsulation dot1q",
                "switchport mode trunk",
                "switchport nonegotiate",
                f"switchport trunk allowed vlan {allowed_vlans}",
            ])
            if poe_mode:
                p_str = str(poe_mode).lower()
                if "never" in p_str:
                    cmds.append("power inline never")
                elif "auto" in p_str:
                    cmds.append("power inline auto")
            cmds.append("no shutdown" if is_active else "shutdown")
            cmds.append("exit")
        return cmds

    # ------------------------------------------------------------------
    # Telefonia IP — Cisco CME (Communications Manager Express - SIP)
    # ------------------------------------------------------------------

    @staticmethod
    def get_cme_pool_reset(pool_id: Union[str, int]) -> List[str]:
        """
        Retorna comandos Cisco IOS para reiniciar (reset) o aparelho SIP
        associado ao pool informado.
        """
        return [
            f"voice register pool {pool_id}",
            "reset",
            "exit",
        ]

    @staticmethod
    def get_cme_dn_name_config(dn_id: Union[str, int], novo_nome: str) -> List[str]:
        """
        Retorna comandos Cisco IOS para alterar o nome/Caller ID associado ao ramal (DN).
        """
        return [
            f"voice register dn {dn_id}",
            f"name {novo_nome}",
            "exit",
        ]


# ---------------------------------------------------------------------------
# Estruturas e Funções Legadas (para compatibilidade com componentes existentes)
# ---------------------------------------------------------------------------

@dataclass
class VlanConfig:
    """Representa uma VLAN a ser criada no switch."""
    vlan_id: int
    nome: str


@dataclass
class ConfiguracaoSwitch:
    """Agrupa todos os parâmetros de configuração vindos da interface gráfica."""
    hostname: str
    enable_secret: str
    vlan_gerencia_id: int
    ip_gerencia: str
    mascara_gerencia: str
    gateway_padrao: str
    modelo: str
    vlans_adicionais: List[VlanConfig] = field(default_factory=list)


MODELOS_DISPONIVEIS = [
    "Cisco Catalyst 2960",
    "Cisco Catalyst 2960-S",
    "Cisco Catalyst 2960-X",
]


def gerar_comandos(cfg: ConfiguracaoSwitch) -> List[str]:
    """Gera lista Day-0 baseada na configuração informada."""
    return CiscoTemplates.get_standard_day0_config(
        hostname=cfg.hostname,
        vlan_id=cfg.vlan_gerencia_id,
        ip=cfg.ip_gerencia,
        mascara=cfg.mascara_gerencia,
        gateway=cfg.gateway_padrao,
    )
