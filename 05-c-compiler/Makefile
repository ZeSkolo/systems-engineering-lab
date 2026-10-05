CC = gcc
CFLAGS = -std=c11 -Wall -Wextra -g
SRC = src/minic.c
BIN = minic

.PHONY: all test clean

all: $(BIN)

$(BIN): $(SRC)
	$(CC) $(CFLAGS) -o $(BIN) $(SRC)

test: $(BIN)
	python3 tests/run_examples.py

clean:
	rm -f $(BIN) /tmp/minic-*
