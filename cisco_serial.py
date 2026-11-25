"""
cisco_serial.py
---------------
Classe responsável por toda a comunicação serial com switches Cisco.
Gerencia abertura/fechamento da porta, envio de comandos síncronos,
leitura contínua em tempo real, proteção contra comandos cegos (SwitchAcessoNegadoException)
e tratamento especial de sincronismo para geração de chaves RSA.
"""

import serial
import serial.tools.list_ports
import threading
import time
import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


class SwitchAcessoNegadoException(Exception):
    """
    Lançada quando o switch já possui credenciais de acesso anteriores (Username/Password),
    impedindo a execução cega do provisionamento Day-0.
    """
    pass


class CiscoSerial:
    """
    Encapsula a conexão serial com um switch Cisco via porta COM/USB-Serial.

    Atributos
    ----------
    porta      : Nome da porta (ex: 'COM3', '/dev/ttyUSB0')
    baudrate   : Taxa de transmissão (padrão 9600)
    timeout    : Tempo máximo de espera por resposta, em segundos
    conectado  : Flag booleana que indica se a sessão está aberta
    """

    INTERVALO_ENTRE_CMDS: float = 0.5   # segundos entre comandos normais
    INTERVALO_LENTO: float = 3.0        # segundos para comandos pesados (write memory)
    TIMEOUT_RSA: float = 30.0           # tempo máximo para geração de par de chaves RSA

    def __init__(
        self,
        porta: str,
        baudrate: int = 9600,
        timeout: float = 2.0,
        callback_log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.porta = porta
        self.baudrate = baudrate
        self.timeout = timeout
        self._callback_log = callback_log
        self._serial: Optional[serial.Serial] = None
        self.conectado: bool = False
        self._lock = threading.Lock()   # garante acesso exclusivo nas escritas/leituras

    @property
    def serial_conn(self) -> Optional[serial.Serial]:
        """Retorna o objeto serial.Serial subjacente."""
        return self._serial

    # ------------------------------------------------------------------
    # Ciclo de Vida da Conexão
    # ------------------------------------------------------------------

    def conectar(self) -> None:
        """
        Abre a porta serial com parâmetros padrão Cisco (8N1, sem controle de fluxo).
        Lança serial.SerialException em caso de falha.
        """
        try:
            self._serial = serial.Serial(
                port=self.porta,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
                xonxoff=False,
                rtscts=False,   # OBRIGATÓRIO: Impede o bypass de credenciais no Cisco
                dsrdtr=False,   # OBRIGATÓRIO: Impede o bypass de credenciais no Cisco
            )
            # Desativa explicitamente DTR e RTS para evitar que o Cisco IOS
            # assuma sessão anterior ativa de console e ignore o login/banner
            try:
                self._serial.dtr = False
                self._serial.rts = False
            except Exception:
                pass

            self.conectado = True
            self._log(f"Porta {self.porta} aberta @ {self.baudrate} baud.", "sucesso")
        except serial.SerialException as exc:
            self.conectado = False
            self._log(f"Falha ao abrir {self.porta}: {exc}", "erro")
            raise

    def desconectar(self) -> None:
        """Fecha a porta serial com segurança, forçando o logout da sessão do console Cisco."""
        if self._serial and self._serial.is_open:
            try:
                # Força o logout da sessão do console Cisco antes de desligar
                with self._lock:
                    self._serial.write(b"\r\nexit\r\n")
                    self._serial.flush()
                time.sleep(0.5)
            except Exception:
                pass
            finally:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
                self.conectado = False
                self._log(f"Porta {self.porta} fechada (sessão deslogada).", "info")

    # ------------------------------------------------------------------
    # Segurança de Buffer e Prevenção de Comandos Cegos
    # ------------------------------------------------------------------

    def verificar_seguranca_inicial(self) -> None:
        """
        Acorda o console e analisa o buffer inicial do switch:
        1. Se identificar "Would you like to enter the initial configuration dialog?",
           envia 'no\r\n' e aguarda o switch retornar ao prompt.
        2. Se identificar 'Username:' ou 'Password:', lança SwitchAcessoNegadoException
           para abortar o provisionamento imediatamente.
        """
        if not self.conectado or not self._serial:
            raise serial.SerialException("Não há conexão serial ativa.")

        self._log("Verificando segurança inicial do switch...", "info")

        with self._lock:
            # Envia quebras de linha para acordar a sessão
            self._serial.write(b"\r\n\r\n")
            self._serial.flush()
            time.sleep(1.5)

            resposta = self._ler_buffer_completo(timeout=3.0)
            if resposta:
                self._log(resposta.strip(), "recebido")

            resp_lower = resposta.lower()

            # Verificação crítica: switch com configuração existente exigindo login
            if "username:" in resp_lower or "password:" in resp_lower:
                msg_erro = (
                    "ACESSO NEGADO: O switch possui configurações antigas e exige login. "
                    "Realize o reset físico (Botão Mode) ou limpe o equipamento antes de rodar o Day-0."
                )
                self._log(msg_erro, "erro")
                raise SwitchAcessoNegadoException(msg_erro)

            # Verificação do assistente de configuração inicial
            if "would you like to enter the initial configuration dialog" in resp_lower:
                self._log("Assistente inicial detectado. Respondendo 'no'...", "info")
                self._serial.write(b"no\r\n")
                self._serial.flush()
                time.sleep(4.0)

                saida = self._ler_buffer_completo(timeout=4.0)
                if saida:
                    self._log(saida.strip(), "recebido")

                if "username:" in saida.lower() or "password:" in saida.lower():
                    msg_erro = (
                        "ACESSO NEGADO: O switch possui configurações antigas e exige login. "
                        "Realize o reset físico (Botão Mode) ou limpe o equipamento antes de rodar o Day-0."
                    )
                    self._log(msg_erro, "erro")
                    raise SwitchAcessoNegadoException(msg_erro)

                self._log("Assistente inicial recusado com sucesso.", "sucesso")

    # ------------------------------------------------------------------
    # Envio e Leitura de Comandos
    # ------------------------------------------------------------------

    def enviar_comando(self, comando: str, lento: bool = False) -> str:
        """
        Envia um único comando para o switch e lê a resposta imediata.
        """
        if not self.conectado or not self._serial:
            raise serial.SerialException("Não há conexão serial ativa.")

        with self._lock:
            try:
                cmd_limpo = comando.strip()
                if cmd_limpo:
                    self._log(f">> {cmd_limpo}", "enviado")

                self._serial.write(comando.encode("utf-8", errors="replace"))
                self._serial.flush()

                pausa = self.INTERVALO_LENTO if lento else self.INTERVALO_ENTRE_CMDS
                time.sleep(pausa)

                resposta = self._ler_buffer()
                if resposta:
                    self._log(resposta, "recebido")
                return resposta

            except serial.SerialException as exc:
                self._log(f"Erro serial durante envio: {exc}", "erro")
                self.conectado = False
                raise

    def enviar_rsa(self, comando: str) -> str:
        """
        Envia o comando de geração RSA e aguarda ativamente no buffer o retorno
        da string '[OK]' (ou timeout com sleep de segurança), evitando que o buffer
        do switch seja sobrecarregado antes do envio dos comandos seguintes.
        """
        if not self.conectado or not self._serial:
            raise serial.SerialException("Não há conexão serial ativa.")

        with self._lock:
            cmd_limpo = comando.strip()
            self._log(f">> {cmd_limpo} (aguardando geração RSA...)", "enviado")
            self._serial.write(comando.encode("utf-8", errors="replace"))
            self._serial.flush()

            resposta_acumulada = ""
            prazo = time.monotonic() + self.TIMEOUT_RSA

            # Pausa inicial mínima para processamento
            time.sleep(2.0)

            while time.monotonic() < prazo:
                time.sleep(0.5)
                trecho = self._ler_buffer()
                if trecho:
                    resposta_acumulada += trecho

                if "[ok]" in resposta_acumulada.lower():
                    self._log("Chave RSA gerada com sucesso [OK].", "sucesso")
                    time.sleep(1.5)
                    break

                if "% " in resposta_acumulada:
                    self._log(f"Aviso na geração RSA: {resposta_acumulada.strip()}", "erro")
                    break

            if not resposta_acumulada:
                # Sleep estendido caso a leitura do buffer tenha ocorrido em thread concorrente
                self._log("Aguardando tempo estendido da chave RSA (8s)...", "info")
                time.sleep(8.0)

            return resposta_acumulada

    # ------------------------------------------------------------------
    # Métodos Auxiliares de Buffer
    # ------------------------------------------------------------------

    def _ler_buffer(self) -> str:
        """Lê os bytes imediatamente disponíveis no buffer."""
        if not self._serial or not self._serial.is_open:
            return ""
        try:
            dados = self._serial.read(self._serial.in_waiting or 1)
            return dados.decode("utf-8", errors="replace")
        except serial.SerialException as exc:
            self._log(f"Erro de leitura: {exc}", "erro")
            return ""

    def _ler_buffer_completo(self, timeout: float = 3.0) -> str:
        """Lê o buffer durante 'timeout' segundos acumulando todos os fragmentos."""
        if not self._serial or not self._serial.is_open:
            return ""
        acumulado = ""
        prazo = time.monotonic() + timeout
        while time.monotonic() < prazo:
            time.sleep(0.2)
            try:
                n = self._serial.in_waiting
                if n:
                    acumulado += self._serial.read(n).decode("utf-8", errors="replace")
            except serial.SerialException:
                break
        return acumulado

    def _log(self, mensagem: str, nivel: str = "info") -> None:
        """Encaminha mensagem para o callback de log da interface gráfica."""
        if self._callback_log:
            self._callback_log(mensagem, nivel)
        else:
            logger.info("[%s] %s", nivel.upper(), mensagem)


# ------------------------------------------------------------------
# Funções Auxiliares Globais
# ------------------------------------------------------------------

def listar_portas() -> List[str]:
    """Retorna lista de portas seriais/COM ativas no sistema operacional."""
    portas = serial.tools.list_ports.comports()
    return sorted([p.device for p in portas])
