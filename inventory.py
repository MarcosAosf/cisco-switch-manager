"""
inventory.py
------------
Gerenciador do cofre de switches e inventário local (Hosts Vault).
Armazena exclusivamente metadados de rede: nome, ip, usuário e grupo.

REGRA DE SEGURANÇA CRÍTICA:
PROIBIDO SALVAR SENHAS EM DISCO.
Nenhuma credencial ou senha é persistida neste arquivo ou em disco,
mantendo a arquitetura de Memória Volátil do PROVISIONADOR DE SWITCH.
"""

import json
import os
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

INVENTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hosts.json")

DEFAULT_INVENTORY: Dict[str, Any] = {
    "groups": ["Core", "Acesso"],
    "hosts": [
        {"name": "SWITCH-CORE-01", "ip": "192.0.2.10", "user": "admin", "group": "Core"},
        {"name": "SW-ACESSO-01", "ip": "198.51.100.15", "user": "admin", "group": "Acesso"}
    ]
}


def _sanitizar_host(host_data: Dict[str, Any]) -> Dict[str, str]:
    """Remove qualquer tentativa de inclusão de campos de senha ou dados sensíveis."""
    chaves_proibidas = {"password", "senha", "secret", "pass", "token", "credencial"}
    return {
        "name": str(host_data.get("name", "")).strip(),
        "ip": str(host_data.get("ip", "")).strip(),
        "user": str(host_data.get("user", "")).strip() or "admin",
        "group": str(host_data.get("group", "")).strip() or "Gerais",
    }


def carregar_inventario() -> Dict[str, Any]:
    """
    Carrega o inventário de switches do arquivo hosts.json.
    Se o arquivo não existir, cria-o automaticamente com a estrutura padrão.
    """
    if not os.path.exists(INVENTORY_FILE):
        salvar_inventario(DEFAULT_INVENTORY)
        return DEFAULT_INVENTORY

    try:
        with open(INVENTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Garante estrutura mínima
        if not isinstance(data, dict):
            data = {"groups": ["Gerais"], "hosts": []}

        if "groups" not in data or not isinstance(data["groups"], list):
            data["groups"] = ["Gerais"]

        if "hosts" not in data or not isinstance(data["hosts"], list):
            data["hosts"] = []

        # Sanitiza todos os hosts garantindo que não existam senhas
        data["hosts"] = [_sanitizar_host(h) for h in data["hosts"] if isinstance(h, dict) and h.get("ip")]

        return data
    except Exception as exc:
        logger.error("Erro ao ler hosts.json: %s. Recriando estrutura padrão.", exc)
        salvar_inventario(DEFAULT_INVENTORY)
        return DEFAULT_INVENTORY


def salvar_inventario(data: Dict[str, Any]) -> bool:
    """
    Salva o inventário em hosts.json após rigorosa sanitização de segurança.
    """
    try:
        hosts_limpos = []
        for h in data.get("hosts", []):
            if isinstance(h, dict) and h.get("ip"):
                hosts_limpos.append(_sanitizar_host(h))

        grupos = sorted(list(set(str(g).strip() for g in data.get("groups", ["Gerais"]) if str(g).strip())))
        if "Gerais" not in grupos:
            grupos.insert(0, "Gerais")

        conteudo_seguro = {
            "groups": grupos,
            "hosts": hosts_limpos
        }

        with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(conteudo_seguro, f, indent=4, ensure_ascii=False)
        return True
    except Exception as exc:
        logger.error("Erro ao salvar hosts.json: %s", exc)
        return False


def obter_hosts() -> List[Dict[str, str]]:
    """Retorna a lista de hosts salvos no inventário."""
    return carregar_inventario().get("hosts", [])


def obter_grupos() -> List[str]:
    """Retorna os grupos disponíveis no inventário."""
    return carregar_inventario().get("groups", ["Gerais"])


def adicionar_host(name: str, ip: str, user: str = "admin", group: str = "Gerais") -> bool:
    """Adiciona um novo switch ao inventário."""
    data = carregar_inventario()
    novo_host = _sanitizar_host({
        "name": name,
        "ip": ip,
        "user": user,
        "group": group,
    })

    if not novo_host["ip"] or not novo_host["name"]:
        return False

    # Evita IP duplicado substituindo dados se já existir ou adicionando novo
    hosts_atualizados = [h for h in data["hosts"] if h["ip"] != novo_host["ip"]]
    hosts_atualizados.append(novo_host)
    data["hosts"] = hosts_atualizados

    if group and group not in data["groups"]:
        data["groups"].append(group)

    return salvar_inventario(data)


def remover_host(ip: str) -> bool:
    """Remove um switch do inventário pelo IP."""
    data = carregar_inventario()
    hosts_antes = len(data["hosts"])
    data["hosts"] = [h for h in data["hosts"] if h.get("ip") != ip.strip()]
    if len(data["hosts"]) != hosts_antes:
        return salvar_inventario(data)
    return False
