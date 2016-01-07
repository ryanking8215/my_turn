# Description
Make the tcp server in the local network can be accessed in internet by using the turn server
Protocol is referenced by rfc6062. But it is not rfc6062 implementation.

# Usage
## server
```python
python3 -m my_turn.server [hostname]

# like
python3 -m my_turn.server www.mydomain.com
```

server is running on the internet, default listen port is 5555


## client
```python
python3 my_turn/client.py [host] [port] [lport]

# host        turn server host
# port        turn server port
# lport       local server port


# like
python3 my_turn/client.py www.mydomain.com 5555 22

```

client is running on your local network server, lport is your local server which wanted to be accessed.
`22` is default port of ssh service

If successful, client will get the relay-server port, and it will showed in server side too.
Then you can use `ssh www.mydomain.com -p [relay-server port]` to connect your local server.
