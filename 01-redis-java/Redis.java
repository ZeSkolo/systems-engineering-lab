import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Mini-Redis — Java NIO TCP server + RESP codec + concurrent KV store + TTL.
 *
 * Run with a JDK:  java Redis.java --port 6379
 * Compatible with redis-cli and any RESP client.
 *
 * Commands: PING, ECHO, SET, GET, DEL, EXISTS, EXPIRE, PEXPIRE, TTL, PTTL,
 *           KEYS, FLUSHDB, INCR, DECR, TYPE, MGET, MSET, DBSIZE
 */
public class Redis {
    public static void main(String[] args) throws Exception {
        int port = 6379;
        for (int i = 0; i < args.length; i++) {
            if ("--port".equals(args[i]) && i + 1 < args.length) {
                port = Integer.parseInt(args[++i]);
            }
        }
        new RedisServer(port, new RedisStore()).serve();
    }
}

final class RedisServer {
    private final int port;
    private final RedisStore store;
    private final CommandProcessor commands;

    RedisServer(int port, RedisStore store) {
        this.port = port;
        this.store = store;
        this.commands = new CommandProcessor(store);
    }

    void serve() throws IOException {
        store.startExpiryDaemon();
        try (Selector selector = Selector.open();
             ServerSocketChannel server = ServerSocketChannel.open()) {
            server.bind(new InetSocketAddress(port));
            server.configureBlocking(false);
            server.register(selector, SelectionKey.OP_ACCEPT);
            System.out.println("mini-redis listening on 0.0.0.0:" + port + " (RESP)");

            while (true) {
                selector.select();
                Iterator<SelectionKey> it = selector.selectedKeys().iterator();
                while (it.hasNext()) {
                    SelectionKey key = it.next();
                    it.remove();
                    try {
                        if (!key.isValid()) {
                            continue;
                        }
                        if (key.isAcceptable()) {
                            accept(selector, server);
                        } else if (key.isReadable()) {
                            read(key);
                        } else if (key.isWritable()) {
                            write(key);
                        }
                    } catch (IOException ex) {
                        closeQuietly(key);
                    }
                }
            }
        } finally {
            store.stopExpiryDaemon();
        }
    }

    private void accept(Selector selector, ServerSocketChannel server) throws IOException {
        SocketChannel client = server.accept();
        if (client == null) {
            return;
        }
        client.configureBlocking(false);
        ClientSession session = new ClientSession();
        client.register(selector, SelectionKey.OP_READ, session);
    }

    private void read(SelectionKey key) throws IOException {
        SocketChannel channel = (SocketChannel) key.channel();
        ClientSession session = (ClientSession) key.attachment();
        int n = channel.read(session.incoming);
        if (n == -1) {
            closeQuietly(key);
            return;
        }
        if (n == 0) {
            return;
        }
        session.incoming.flip();
        session.parser.append(session.incoming);
        session.incoming.compact();

        Object value;
        while ((value = session.parser.next()) != RespParser.NEED_MORE) {
            byte[] reply = commands.dispatch(value);
            session.outgoing.write(reply);
        }
        if (session.outgoing.size() > 0) {
            key.interestOps(key.interestOps() | SelectionKey.OP_WRITE);
        }
    }

    private void write(SelectionKey key) throws IOException {
        SocketChannel channel = (SocketChannel) key.channel();
        ClientSession session = (ClientSession) key.attachment();
        session.outgoing.flip();
        channel.write(session.outgoing.buffer());
        session.outgoing.compact();
        if (session.outgoing.size() == 0) {
            key.interestOps(SelectionKey.OP_READ);
        }
    }

    private static void closeQuietly(SelectionKey key) {
        try {
            key.cancel();
            key.channel().close();
        } catch (IOException ignored) {
        }
    }
}

final class ClientSession {
    final ByteBuffer incoming = ByteBuffer.allocate(64 * 1024);
    final GrowableBuffer outgoing = new GrowableBuffer();
    final RespParser parser = new RespParser();
}

final class GrowableBuffer {
    private ByteBuffer buf = ByteBuffer.allocate(8 * 1024);

    void write(byte[] data) {
        ensure(data.length);
        buf.put(data);
    }

    void flip() {
        buf.flip();
    }

    void compact() {
        buf.compact();
    }

    ByteBuffer buffer() {
        return buf;
    }

    int size() {
        return buf.position();
    }

    private void ensure(int extra) {
        if (buf.remaining() >= extra) {
            return;
        }
        ByteBuffer next = ByteBuffer.allocate(Math.max(buf.capacity() * 2, buf.position() + extra));
        buf.flip();
        next.put(buf);
        buf = next;
    }
}

/** Streaming RESP parser. Also accepts Redis inline/telnet commands. */
final class RespParser {
    static final Object NEED_MORE = new Object();
    private final StringBuilder raw = new StringBuilder();

    void append(ByteBuffer src) {
        while (src.hasRemaining()) {
            raw.append((char) (src.get() & 0xff));
        }
    }

    Object next() {
        if (raw.length() == 0) {
            return NEED_MORE;
        }
        try {
            Parse p = parseValue(0);
            if (p == null) {
                return NEED_MORE;
            }
            raw.delete(0, p.end);
            return p.value;
        } catch (RespSyntaxException ex) {
            raw.setLength(0);
            return new RespError(ex.getMessage());
        }
    }

    private Parse parseValue(int i) {
        if (i >= raw.length()) {
            return null;
        }
        char c = raw.charAt(i);
        switch (c) {
            case '+':
                return parseSimple(i + 1, false);
            case '-': {
                Parse s = parseSimple(i + 1, false);
                if (s == null) {
                    return null;
                }
                return new Parse(new RespError(String.valueOf(s.value)), s.end);
            }
            case ':': {
                Parse s = parseSimple(i + 1, false);
                if (s == null) {
                    return null;
                }
                return new Parse(Long.parseLong(String.valueOf(s.value)), s.end);
            }
            case '$':
                return parseBulk(i + 1);
            case '*':
                return parseArray(i + 1);
            default:
                return parseInline(i);
        }
    }

    private Parse parseSimple(int i, boolean bulk) {
        int cr = indexOfCrlf(i);
        if (cr < 0) {
            return null;
        }
        return new Parse(raw.substring(i, cr), cr + 2);
    }

    private Parse parseBulk(int i) {
        int cr = indexOfCrlf(i);
        if (cr < 0) {
            return null;
        }
        int len = Integer.parseInt(raw.substring(i, cr));
        if (len == -1) {
            return new Parse(null, cr + 2);
        }
        int start = cr + 2;
        int end = start + len;
        if (end + 2 > raw.length()) {
            return null;
        }
        if (raw.charAt(end) != '\r' || raw.charAt(end + 1) != '\n') {
            throw new RespSyntaxException("ERR protocol: bulk string missing CRLF");
        }
        return new Parse(raw.substring(start, end), end + 2);
    }

    private Parse parseArray(int i) {
        int cr = indexOfCrlf(i);
        if (cr < 0) {
            return null;
        }
        int n = Integer.parseInt(raw.substring(i, cr));
        if (n == -1) {
            return new Parse(null, cr + 2);
        }
        int pos = cr + 2;
        List<Object> items = new ArrayList<>(n);
        for (int k = 0; k < n; k++) {
            Parse item = parseValue(pos);
            if (item == null) {
                return null;
            }
            items.add(item.value);
            pos = item.end;
        }
        return new Parse(items, pos);
    }

    private Parse parseInline(int i) {
        int cr = indexOfCrlf(i);
        if (cr < 0) {
            return null;
        }
        String line = raw.substring(i, cr).trim();
        if (line.isEmpty()) {
            raw.delete(i, cr + 2);
            return NEED_MORE == null ? null : null;
        }
        String[] parts = line.split("\\s+");
        List<Object> items = new ArrayList<>();
        for (String p : parts) {
            items.add(p);
        }
        return new Parse(items, cr + 2);
    }

    private int indexOfCrlf(int from) {
        for (int i = from; i + 1 < raw.length(); i++) {
            if (raw.charAt(i) == '\r' && raw.charAt(i + 1) == '\n') {
                return i;
            }
        }
        return -1;
    }

    private static final class Parse {
        final Object value;
        final int end;

        Parse(Object value, int end) {
            this.value = value;
            this.end = end;
        }
    }
}

final class RespSyntaxException extends RuntimeException {
    RespSyntaxException(String msg) {
        super(msg);
    }
}

final class RespError {
    final String message;

    RespError(String message) {
        this.message = message;
    }
}

final class RespWriter {
    private static final byte[] CRLF = new byte[] {'\r', '\n'};

    static byte[] simple(String s) {
        return ("+" + s + "\r\n").getBytes(StandardCharsets.UTF_8);
    }

    static byte[] error(String s) {
        return ("-" + s + "\r\n").getBytes(StandardCharsets.UTF_8);
    }

    static byte[] integer(long v) {
        return (":" + v + "\r\n").getBytes(StandardCharsets.UTF_8);
    }

    static byte[] bulk(String s) {
        if (s == null) {
            return "$-1\r\n".getBytes(StandardCharsets.UTF_8);
        }
        byte[] body = s.getBytes(StandardCharsets.UTF_8);
        byte[] header = ("$" + body.length + "\r\n").getBytes(StandardCharsets.UTF_8);
        byte[] out = new byte[header.length + body.length + 2];
        System.arraycopy(header, 0, out, 0, header.length);
        System.arraycopy(body, 0, out, header.length, body.length);
        out[out.length - 2] = '\r';
        out[out.length - 1] = '\n';
        return out;
    }

    static byte[] array(List<String> items) {
        StringBuilder sb = new StringBuilder();
        sb.append('*').append(items.size()).append("\r\n");
        for (String item : items) {
            if (item == null) {
                sb.append("$-1\r\n");
            } else {
                byte[] body = item.getBytes(StandardCharsets.UTF_8);
                sb.append('$').append(body.length).append("\r\n");
                sb.append(item).append("\r\n");
            }
        }
        return sb.toString().getBytes(StandardCharsets.UTF_8);
    }
}

final class RedisStore {
    private static final class Entry {
        volatile String value;
        volatile long expireAtMs; // 0 = no expiry

        Entry(String value, long expireAtMs) {
            this.value = value;
            this.expireAtMs = expireAtMs;
        }
    }

    private final ConcurrentHashMap<String, Entry> map = new ConcurrentHashMap<>();
    private volatile boolean running = true;
    private Thread daemon;

    void startExpiryDaemon() {
        daemon = new Thread(this::activeExpireLoop, "mini-redis-expire");
        daemon.setDaemon(true);
        daemon.start();
    }

    void stopExpiryDaemon() {
        running = false;
        if (daemon != null) {
            daemon.interrupt();
        }
    }

    private void activeExpireLoop() {
        while (running) {
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                return;
            }
            if (map.isEmpty()) {
                continue;
            }
            // Redis-style: sample random keys and drop expired ones.
            int samples = Math.min(20, map.size());
            List<String> keys = new ArrayList<>(map.keySet());
            long now = System.currentTimeMillis();
            for (int i = 0; i < samples; i++) {
                String key = keys.get(ThreadLocalRandom.current().nextInt(keys.size()));
                Entry e = map.get(key);
                if (e != null && e.expireAtMs > 0 && e.expireAtMs <= now) {
                    map.remove(key, e);
                }
            }
        }
    }

    private Entry live(String key) {
        Entry e = map.get(key);
        if (e == null) {
            return null;
        }
        if (e.expireAtMs > 0 && e.expireAtMs <= System.currentTimeMillis()) {
            map.remove(key, e);
            return null;
        }
        return e;
    }

    String get(String key) {
        Entry e = live(key);
        return e == null ? null : e.value;
    }

    boolean set(String key, String value, Long pxMs, boolean nx, boolean xx) {
        Entry cur = live(key);
        if (nx && cur != null) {
            return false;
        }
        if (xx && cur == null) {
            return false;
        }
        long exp = pxMs == null ? 0L : System.currentTimeMillis() + pxMs;
        map.put(key, new Entry(value, exp));
        return true;
    }

    int del(List<String> keys) {
        int n = 0;
        for (String k : keys) {
            if (live(k) != null && map.remove(k) != null) {
                n++;
            }
        }
        return n;
    }

    int exists(List<String> keys) {
        int n = 0;
        for (String k : keys) {
            if (live(k) != null) {
                n++;
            }
        }
        return n;
    }

    boolean expire(String key, long pxMs) {
        Entry e = live(key);
        if (e == null) {
            return false;
        }
        e.expireAtMs = System.currentTimeMillis() + pxMs;
        return true;
    }

    long ttlMs(String key) {
        Entry e = live(key);
        if (e == null) {
            return -2;
        }
        if (e.expireAtMs == 0) {
            return -1;
        }
        return Math.max(0, e.expireAtMs - System.currentTimeMillis());
    }

    List<String> keys(String glob) {
        List<String> out = new ArrayList<>();
        String regex = globToRegex(glob);
        for (String k : map.keySet()) {
            if (live(k) != null && k.matches(regex)) {
                out.add(k);
            }
        }
        return out;
    }

    void flush() {
        map.clear();
    }

    long incr(String key, long delta) {
        while (true) {
            Entry e = live(key);
            long next;
            if (e == null) {
                next = delta;
                if (map.putIfAbsent(key, new Entry(Long.toString(next), 0)) == null) {
                    return next;
                }
                continue;
            }
            long cur;
            try {
                cur = Long.parseLong(e.value);
            } catch (NumberFormatException ex) {
                throw new IllegalStateException("ERR value is not an integer or out of range");
            }
            next = cur + delta;
            e.value = Long.toString(next);
            return next;
        }
    }

    int size() {
        int n = 0;
        for (String k : map.keySet()) {
            if (live(k) != null) {
                n++;
            }
        }
        return n;
    }

    private static String globToRegex(String glob) {
        StringBuilder sb = new StringBuilder();
        sb.append('^');
        for (int i = 0; i < glob.length(); i++) {
            char c = glob.charAt(i);
            switch (c) {
                case '*':
                    sb.append(".*");
                    break;
                case '?':
                    sb.append('.');
                    break;
                default:
                    if ("\\.^$+{}[]|()".indexOf(c) >= 0) {
                        sb.append('\\');
                    }
                    sb.append(c);
            }
        }
        sb.append('$');
        return sb.toString();
    }
}

final class CommandProcessor {
    private final RedisStore store;

    CommandProcessor(RedisStore store) {
        this.store = store;
    }

    byte[] dispatch(Object value) {
        if (value instanceof RespError) {
            return RespWriter.error(((RespError) value).message);
        }
        if (!(value instanceof List)) {
            return RespWriter.error("ERR unknown command encoding");
        }
        @SuppressWarnings("unchecked")
        List<Object> parts = (List<Object>) value;
        if (parts.isEmpty()) {
            return RespWriter.error("ERR empty command");
        }
        String cmd = String.valueOf(parts.get(0)).toUpperCase(Locale.ROOT);
        try {
            switch (cmd) {
                case "PING":
                    if (parts.size() == 1) {
                        return RespWriter.simple("PONG");
                    }
                    return RespWriter.bulk(str(parts, 1));
                case "ECHO":
                    require(parts, 2, cmd);
                    return RespWriter.bulk(str(parts, 1));
                case "SET":
                    return set(parts);
                case "GET":
                    require(parts, 2, cmd);
                    return RespWriter.bulk(store.get(str(parts, 1)));
                case "DEL":
                    if (parts.size() < 2) {
                        return RespWriter.error("ERR wrong number of arguments for 'del' command");
                    }
                    return RespWriter.integer(store.del(strs(parts, 1)));
                case "EXISTS":
                    if (parts.size() < 2) {
                        return RespWriter.error("ERR wrong number of arguments for 'exists' command");
                    }
                    return RespWriter.integer(store.exists(strs(parts, 1)));
                case "EXPIRE":
                    require(parts, 3, cmd);
                    return RespWriter.integer(store.expire(str(parts, 1), Long.parseLong(str(parts, 2)) * 1000L) ? 1 : 0);
                case "PEXPIRE":
                    require(parts, 3, cmd);
                    return RespWriter.integer(store.expire(str(parts, 1), Long.parseLong(str(parts, 2))) ? 1 : 0);
                case "TTL": {
                    require(parts, 2, cmd);
                    long ms = store.ttlMs(str(parts, 1));
                    if (ms < 0) {
                        return RespWriter.integer(ms);
                    }
                    return RespWriter.integer((ms + 999) / 1000);
                }
                case "PTTL":
                    require(parts, 2, cmd);
                    return RespWriter.integer(store.ttlMs(str(parts, 1)));
                case "KEYS":
                    require(parts, 2, cmd);
                    return RespWriter.array(store.keys(str(parts, 1)));
                case "FLUSHDB":
                case "FLUSHALL":
                    store.flush();
                    return RespWriter.simple("OK");
                case "INCR":
                    require(parts, 2, cmd);
                    return RespWriter.integer(store.incr(str(parts, 1), 1));
                case "DECR":
                    require(parts, 2, cmd);
                    return RespWriter.integer(store.incr(str(parts, 1), -1));
                case "INCRBY":
                    require(parts, 3, cmd);
                    return RespWriter.integer(store.incr(str(parts, 1), Long.parseLong(str(parts, 2))));
                case "TYPE":
                    require(parts, 2, cmd);
                    return RespWriter.simple(store.get(str(parts, 1)) == null ? "none" : "string");
                case "MGET": {
                    if (parts.size() < 2) {
                        return RespWriter.error("ERR wrong number of arguments for 'mget' command");
                    }
                    List<String> vals = new ArrayList<>();
                    for (int i = 1; i < parts.size(); i++) {
                        vals.add(store.get(str(parts, i)));
                    }
                    return RespWriter.array(vals);
                }
                case "MSET": {
                    if (parts.size() < 3 || ((parts.size() - 1) % 2) != 0) {
                        return RespWriter.error("ERR wrong number of arguments for 'mset' command");
                    }
                    for (int i = 1; i < parts.size(); i += 2) {
                        store.set(str(parts, i), str(parts, i + 1), null, false, false);
                    }
                    return RespWriter.simple("OK");
                }
                case "DBSIZE":
                    return RespWriter.integer(store.size());
                case "QUIT":
                    return RespWriter.simple("OK");
                case "COMMAND":
                    return RespWriter.array(List.of());
                default:
                    return RespWriter.error("ERR unknown command '" + cmd + "'");
            }
        } catch (IllegalArgumentException | IllegalStateException ex) {
            return RespWriter.error(ex.getMessage());
        } catch (NumberFormatException ex) {
            return RespWriter.error("ERR value is not an integer or out of range");
        }
    }

    private byte[] set(List<Object> parts) {
        if (parts.size() < 3) {
            return RespWriter.error("ERR wrong number of arguments for 'set' command");
        }
        String key = str(parts, 1);
        String val = str(parts, 2);
        Long px = null;
        boolean nx = false;
        boolean xx = false;
        for (int i = 3; i < parts.size(); i++) {
            String opt = str(parts, i).toUpperCase(Locale.ROOT);
            switch (opt) {
                case "EX":
                    px = Long.parseLong(str(parts, ++i)) * 1000L;
                    break;
                case "PX":
                    px = Long.parseLong(str(parts, ++i));
                    break;
                case "NX":
                    nx = true;
                    break;
                case "XX":
                    xx = true;
                    break;
                default:
                    return RespWriter.error("ERR syntax error");
            }
        }
        boolean ok = store.set(key, val, px, nx, xx);
        if (!ok) {
            return RespWriter.bulk(null);
        }
        return RespWriter.simple("OK");
    }

    private static void require(List<Object> parts, int n, String cmd) {
        if (parts.size() != n) {
            throw new IllegalArgumentException("ERR wrong number of arguments for '" + cmd.toLowerCase(Locale.ROOT) + "' command");
        }
    }

    private static String str(List<Object> parts, int i) {
        Object v = parts.get(i);
        return v == null ? "" : String.valueOf(v);
    }

    private static List<String> strs(List<Object> parts, int from) {
        List<String> out = new ArrayList<>();
        for (int i = from; i < parts.size(); i++) {
            out.add(str(parts, i));
        }
        return out;
    }
}
