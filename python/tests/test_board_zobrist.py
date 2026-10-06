"""
Unit tests for the incremental zobrist hash in katago.game.board.Board.

Board.zobrist is updated incrementally as stones are placed and captured. These tests check that it always equals
the hash recomputed from scratch for the stones currently on the board, in particular after captures.
"""

import random

import pytest

from katago.game.board import Board


def recomputed_zobrist(board):
    h = 0
    for loc in range(board.arrsize):
        if board.board[loc] == Board.BLACK or board.board[loc] == Board.WHITE:
            h ^= Board.ZOBRIST_STONE[board.board[loc]][loc]
    return h


def test_zobrist_after_single_capture():
    board = Board(9)
    board.play(Board.WHITE, board.loc(1, 0))
    board.play(Board.BLACK, board.loc(0, 0))
    board.play(Board.BLACK, board.loc(2, 0))
    board.play(Board.BLACK, board.loc(1, 1))  # captures the white stone at (1,0)
    assert board.board[board.loc(1, 0)] == Board.EMPTY
    assert board.zobrist == recomputed_zobrist(board)


def test_zobrist_matches_recomputation_in_random_games():
    rand = random.Random(12345)
    for _ in range(100):
        board = Board(5)
        pla = Board.BLACK
        for _ in range(80):
            moves = [loc for loc in range(board.arrsize) if board.board[loc] == Board.EMPTY and board.would_be_legal(pla, loc)]
            if not moves:
                break
            board.play(pla, rand.choice(moves))
            assert board.zobrist == recomputed_zobrist(board)
            pla = Board.get_opp(pla)


def test_zobrist_restored_by_undo():
    rand = random.Random(54321)
    for _ in range(100):
        board = Board(5)
        pla = Board.BLACK
        for _ in range(80):
            moves = [loc for loc in range(board.arrsize) if board.board[loc] == Board.EMPTY and board.would_be_legal(pla, loc)]
            if not moves:
                break
            zobrist_before = board.zobrist
            record = board.playRecordedUnsafe(pla, rand.choice(moves))
            assert board.zobrist == recomputed_zobrist(board)
            board.undo(record)
            assert board.zobrist == zobrist_before
            board.play(pla, rand.choice(moves))
            pla = Board.get_opp(pla)


@pytest.mark.parametrize("pla", [Board.BLACK, Board.WHITE])
def test_chain_capture_matches_fresh_position_and_undo(pla):
    board = Board(9)
    captured = [(1, 1), (2, 1)]
    surrounding = [(0, 1), (1, 0), (2, 0), (3, 1), (1, 2)]
    for point in captured:
        board.set_stone(Board.get_opp(pla), board.loc(*point))
    for point in surrounding:
        board.set_stone(pla, board.loc(*point))
    board.set_pla(pla)
    before = board.copy()
    assert before.zobrist == recomputed_zobrist(before)

    move = board.loc(2, 2)
    assert board.would_be_legal(pla, move)
    record = board.playRecordedUnsafe(pla, move)
    after = board.copy()
    # Undo before checking the captured hash, so a capture error cannot hide an undo error.
    board.undo(record)
    assert list(board.board) == list(before.board)
    assert board.pos_zobrist() == before.pos_zobrist()
    assert board.sit_zobrist() == before.sit_zobrist()
    assert board.num_captures_made == before.num_captures_made
    assert all(after.board[after.loc(*point)] == Board.EMPTY for point in captured)
    assert after.num_captures_made[pla] == before.num_captures_made[pla] + len(captured)

    fresh = Board(9)
    for point in surrounding + [(2, 2)]:
        fresh.set_stone(pla, fresh.loc(*point))
    fresh.set_pla(after.pla)
    assert list(after.board) == list(fresh.board)
    assert after.pos_zobrist() == fresh.pos_zobrist() == recomputed_zobrist(after)
    assert after.sit_zobrist() == fresh.sit_zobrist()


def test_pass_changes_only_the_player_hash():
    board = Board(9)
    board.play(Board.BLACK, board.loc(4, 4))
    position_hash = board.pos_zobrist()
    situation_hash = board.sit_zobrist()
    board.play(board.pla, Board.PASS_LOC)
    assert board.pos_zobrist() == position_hash == recomputed_zobrist(board)
    assert board.sit_zobrist() == position_hash ^ Board.ZOBRIST_PLA[board.pla]
    assert board.sit_zobrist() != situation_hash
