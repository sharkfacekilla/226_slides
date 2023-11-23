from logging import getLogger, DEBUG
from logging.handlers import SysLogHandler
from random import randrange
from socket import socket, AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR
from struct import pack
from threading import Semaphore, Thread
from Board import Board, Direction
from View import display, display_scores

import constants


class Game:
    def __init__(self):
        self.BUF_SIZE = 1
        self.HOST = ''
        self.QUEUE_SIZE = 1

        self.BOARD_SIZE = 10
        self.NUM_TREASURES = 5
        self.MIN_TREASURE = 5
        self.MAX_TREASURE = 10
        self.MAX_PLAYERS = 2

        self.board = Board(self.BOARD_SIZE, self.NUM_TREASURES, self.MIN_TREASURE, self.MAX_TREASURE, self.MAX_PLAYERS)
        self._place_player(constants.PLAYER1_NAME)
        self._place_player(constants.PLAYER2_NAME)

        self.quitting = False

        self.lock = Semaphore()
        self.num_clients = 0

        self.logger = getLogger('226_game')
        self.logger.setLevel(DEBUG)  # Debug messages are normally filtered
        handler = SysLogHandler(address='/dev/log')  # Connect to log sys
        self.logger.addHandler(handler)

    def _place_player(self, name: str) -> None:
        """
        Place a player with the given name on the board at a free location.

        This method must not be called more than twice, or an infinite loop will result.
        :param name: The name of the player that should be created; must be valid and unique
        """
        while True:
            try:
                self.board.add_player(name, randrange(self.BOARD_SIZE), randrange(self.BOARD_SIZE))
                return
            except ValueError:
                continue

    def _generate_score_list(self) -> bytes:
        """
        Generates the list of scores for transmission

        :return: The list of scores
        """
        score_list = self.board.get_score_list()
        reply = pack('!H', score_list[0]) + pack('!H', score_list[1])
        return reply

    def _is_game_over(self) -> bytes:
        """
        Checks if the game is over.

        Displays game stats to stdout.
        :return: b'' if the game is not over, or the notification to be transmitted to the client if the game is over
        """
        reply = b''
        with self.lock:
            if self.quitting or self.board.game_over():
                self.logger.debug(self.board)
                display_scores(self.board)
                display(self.board)
                reply = self._generate_score_list() + str(self.board).encode()
        return reply

    def _implement_command(self, cmd: int) -> bytes:
        """
        Implements the given command.

        :param cmd: The command to implement
        :return: The notification to be transmitted to the client
        """
        direction = None
        reply = b''
        with self.lock:
            match cmd & constants.CMD_MASK:
                case constants.UP:
                    direction = Direction.UP
                case constants.LEFT:
                    direction = Direction.LEFT
                case constants.RIGHT:
                    direction = Direction.RIGHT
                case constants.DOWN:
                    direction = Direction.DOWN
                case constants.QUIT:
                    self.quitting = True
                    return constants.OK
                case constants.GET:
                    self.logger.debug(self.board)
                    display_scores(self.board)
                    display(self.board)
                    reply = str(self.board).encode()
                case _:
                    self.logger.warning('Unknown command')
                    return constants.ERROR

            if direction is not None:
                player = cmd & constants.PLAYER_MASK
                match player:
                    case constants.PLAYER1 | constants.PLAYER2:
                        try:
                            val = self.board.move_player(
                                constants.PLAYER1_NAME if player == constants.PLAYER1 else constants.PLAYER2_NAME,
                                direction)
                        except ValueError as details:
                            self.logger.warning(details)
                            return constants.ERROR
                        else:
                            self.logger.debug(self.board)
                            display_scores(self.board)
                            display(self.board)
                            if val != 0:
                                self.logger.debug(f'Score +{val}')
                    case _:
                        self.logger.warning('Unknown player')
                        return constants.ERROR

            reply = self._generate_score_list() + reply
        return reply

    def _send_msg(self, client_socket: socket, msg: bytes) -> None:
        """
        Sends the given message over the given connection, prefaced by a size header.

        Message length must fit into a short integer.
        :param client_socket: The connection to use
        :param msg: The message to transmit
        """
        payload = msg
        header = pack('!H', len(payload))
        segment = header + payload
        self.logger.debug(f'Sending {segment}')
        client_socket.sendall(segment)

    def _interact_with_client(self, client_socket: socket, client_id: int) -> None:
        """
        Implement the commands received via client_socket and send back the results over that same connection.

        Format is:
        ______00
        4 bits for the command (U is 0010, L is 0100, R is 0110, D is 0011, Q is 1000, G is 1111)
        2 bits for the player (Player 1 is 01 and Player 2 is 10) to whom the movement command applies
        2 0 bits

        :param client_socket: The current network connection
        :param client_id: The current client ID
        """
        with client_socket:
            self._send_msg(client_socket, pack('!B', client_id))
            while True:
                data = client_socket.recv(self.BUF_SIZE)
                self.logger.debug(f'Client: {client_id}_{client_socket.getpeername()} Data: {data.hex()}')
                if data == b'':
                    break

                if (reply := self._is_game_over()) != b'':
                    self._send_msg(client_socket, reply)
                    continue

                cmd = int.from_bytes(data, byteorder='big')
                if (reply := self._implement_command(cmd)) != b'':
                    self._send_msg(client_socket, reply)
                    continue

    def start(self) -> None:
        """
        Start the server and process incoming connections.
        """
        with socket(AF_INET, SOCK_STREAM) as sock:
            sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            sock.bind((self.HOST, constants.PORT))
            sock.listen(self.QUEUE_SIZE)
            self.logger.debug(f'Server: {sock.getsockname()}')
            while True:
                sc, _ = sock.accept()
                self.num_clients += 1
                if self.num_clients > self.MAX_PLAYERS:
                    sc.close()
                else:
                    Thread(target=self._interact_with_client, args=(sc, self.num_clients)).start()
