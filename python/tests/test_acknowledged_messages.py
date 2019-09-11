from multiprocessing import Process, Queue
import os
import threading
import time
import pytest

import sys

from twisted.internet import reactor

from acknowledged_udp.udp_client import UdpClient
from acknowledged_udp.udp_server import UdpServer
from acknowledged_udp.config import global_network_config
from acknowledged_udp.protocol import Protocol, MessageType

from test_non_acknowledged_messages import wait_for_test_finished

from rafcon.utils import log
logger = log.get_logger(__name__)
import test_non_acknowledged_messages


SUCCESS_MESSAGE = "success"
FAILURE_MESSAGE = "failure"


def info(title):
    print(title)
    print('module name:', __name__)
    if hasattr(os, 'getppid'):  # only available on Unix
        print('parent process:', os.getppid())
    print('process id:', os.getpid())


##########################################################
# server
##########################################################

server_transport = None


def write_back_message(datagram, address):
    logger.info("Server received datagram {0} from address: {1}".format(str(datagram), str(address)))
    # server_transport.write(datagram, address)


def start_udp_server(name, queue_dict):
    info(name)
    udp_server = UdpServer()
    connector = reactor.listenUDP(global_network_config.get_config_value("SERVER_UDP_PORT"), udp_server)
    udp_server.datagram_received_function = write_back_message

    global server_transport
    server_transport = udp_server.get_transport()

    wait_for_test_finish = threading.Thread(target=wait_for_test_finished, args=[queue_dict,
                                                                                 udp_server,
                                                                                 connector,
                                                                                 True])
    wait_for_test_finish.start()
    # reactor.addSystemEventTrigger('before', 'shutdown', udp_server.some_function)
    reactor.run()
    wait_for_test_finish.join()
    logger.info("Server joined wait_for_test_finish")


##########################################################
# client
##########################################################

number_of_dropped_messages = 0


def send_test_data(udp_client, queue_dict):
    protocols = []

    # Here just register messages are sent: as register messages are acknowledged per default
    # send_message_acknowledged should be successful
    protocols.append(Protocol(MessageType.REGISTER, "registering_with_acks"))
    protocols.append(Protocol(MessageType.REGISTER, "this_is_a_state_id"))
    protocols.append(Protocol(MessageType.REGISTER, "not_the_final_message"))
    # protocols.append(Protocol(MessageType.REGISTER, FINAL_MESSAGE))

    # register for acknowledges in the first message, all subsequent message should then be acknowledged
    protocols.append(Protocol(MessageType.REGISTER_WITH_ACKNOWLEDGES, "Registering with acks"))
    protocols.append(Protocol(MessageType.STATE_ID, "This is a state_id"))
    protocols.append(Protocol(MessageType.COMMAND, test_non_acknowledged_messages.FINAL_MESSAGE))

    while True:
        protocol = protocols.pop(0)
        logger.debug("For unit test send datagram: {0}".format(str(protocol)))
        # TODO: how does twisted know to which endpoint the message should be sent?
        udp_client.send_message_acknowledged(protocol,
                                             (global_network_config.get_config_value("SERVER_IP"),
                                              global_network_config.get_config_value("SERVER_UDP_PORT")),
                                             blocking=True)

        if protocol.message_content == test_non_acknowledged_messages.FINAL_MESSAGE:
            break

        time.sleep(0.1)
    logger.debug("Sender thread finished")

    while udp_client.messages_to_be_acknowledged_pending():
        from test_non_acknowledged_messages import print_highlight
        print_highlight(udp_client._messages_to_be_acknowledged)
        for key, protocoll in udp_client._messages_to_be_acknowledged.iteritems():
            print_highlight(protocoll[0].message_content)
        time.sleep(0.2)

    if udp_client.number_of_dropped_messages == 0:
        queue_dict[test_non_acknowledged_messages.CLIENT_TO_MAIN_QUEUE].put(SUCCESS_MESSAGE)
    else:
        queue_dict[test_non_acknowledged_messages.CLIENT_TO_MAIN_QUEUE].put(FAILURE_MESSAGE)


def start_udp_client(name, queue_dict):
    info(name)
    udp_client = UdpClient()
    connector = reactor.listenUDP(0, udp_client)

    sender_thread = threading.Thread(target=send_test_data, args=[udp_client, queue_dict])
    sender_thread.start()

    wait_for_test_finish = threading.Thread(target=wait_for_test_finished, args=[queue_dict,
                                                                                 udp_client,
                                                                                 connector,
                                                                                 False])
    wait_for_test_finish.start()

    reactor.run()

    sender_thread.join()
    logger.info("Client joined sender_thread")
    wait_for_test_finish.join()
    logger.info("Client joint wait_for_test_finish")


def test_acknowledged_messages():

    from test_non_acknowledged_messages import check_if_ports_are_open
    assert check_if_ports_are_open(), "Address already in use by another server!"

    queue_dict = dict()
    # queue_dict[CLIENT_TO_SERVER_QUEUE] = Queue()
    # queue_dict[SERVER_TO_CLIENT_QUEUE] = Queue()
    # queue_dict[SERVER_TO_MAIN_QUEUE] = Queue()
    queue_dict[test_non_acknowledged_messages.CLIENT_TO_MAIN_QUEUE] = Queue()
    queue_dict[test_non_acknowledged_messages.MAIN_TO_SERVER_QUEUE] = Queue()
    queue_dict[test_non_acknowledged_messages.MAIN_TO_CLIENT_QUEUE] = Queue()

    server = Process(target=start_udp_server, args=("udp_server", queue_dict))
    server.start()

    client = Process(target=start_udp_client, args=("udp_client", queue_dict))
    client.start()

    try:
        data = queue_dict[test_non_acknowledged_messages.CLIENT_TO_MAIN_QUEUE].get(timeout=10)
        if data == SUCCESS_MESSAGE:
            logger.info("Test successful\n\n")
        else:
            logger.error("Test failed\n\n")
        assert data == SUCCESS_MESSAGE
        # send destroy commands to other processes
        queue_dict[test_non_acknowledged_messages.MAIN_TO_SERVER_QUEUE].put(
            test_non_acknowledged_messages.DESTROY_MESSAGE)
        queue_dict[test_non_acknowledged_messages.MAIN_TO_CLIENT_QUEUE].put(
            test_non_acknowledged_messages.DESTROY_MESSAGE)
    except:
        server.terminate()
        client.terminate()
        time.sleep(0.1)
        raise
    finally:
        server.join(10)
        client.join(10)

    # Uninstall reactor to allow further test with custom reactors
    del sys.modules["twisted.internet.reactor"]

    assert not server.is_alive(), "Server is still alive"
    assert not client.is_alive(), "Client is still alive"

if __name__ == '__main__':
    test_acknowledged_messages()
    # pytest.main([__file__])