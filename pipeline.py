import os
import argparse
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm
import zstandard as zstd
import chess
import chess.pgn
import chess.engine
import io

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

def stream_games(zst_path, max_games=None, elo_min=600, elo_max=1400):
    """Yield games from a compressed PGN file, filtered by Elo range.
    Args:
        zst_path: Path to .pgn.zst file.
        max_games: Optional limit on number of games to yield.
        elo_min, elo_max: Inclusive Elo bounds for BOTH players.
    """
    dctx = zstd.ZstdDecompressor()
    with open(zst_path, "rb") as f:
        stream = dctx.stream_reader(f)
        text_stream = io.TextIOWrapper(stream, encoding="utf-8")
        count = 0
        while True:
            game = chess.pgn.read_game(text_stream)
            if game is None:
                break
            try:
                white_elo = int(game.headers.get("WhiteElo", 0))
                black_elo = int(game.headers.get("BlackElo", 0))
            except ValueError:
                continue
            if elo_min <= white_elo <= elo_max and elo_min <= black_elo <= elo_max:
                yield game
                count += 1
                if max_games and count >= max_games:
                    break

def _has_back_rank_mate(board):
    """Return True if the side to move has a forced back-rank mate in one move."""
    for move in board.legal_moves:
        board.push(move)
        if board.is_checkmate():
            board.pop()
            return True
        board.pop()
    return False

def _missed_one_mover(board_before, played_move, best_move):
    """Detect a missed one-move checkmate.
    best_move is the engine-suggested move from the analysis of board_before.
    """
    if not best_move or best_move == played_move:
        return False
    board_test = board_before.copy()
    board_test.push(best_move)
    return board_test.is_checkmate()

def classify_failure(board_before, move, cpl, best_move):
    """Heuristic failure-mode classifier.
    Returns a string label or None if the move is acceptable.
    """
    if cpl < 100:
        return None

    board_after = board_before.copy()
    board_after.push(move)

    # 1. Hanging piece (moved piece is immediately capturable with insufficient defenders)
    to_sq = move.to_square
    if board_after.is_attacked_by(board_after.turn, to_sq):
        defenders = board_after.attackers(not board_after.turn, to_sq)
        attackers = board_after.attackers(board_after.turn, to_sq)
        if len(attackers) > len(defenders):
            return "hanging_piece"

    # 2. Greedy capture (captured material but walked into a worse position)
    if board_before.is_capture(move) and cpl > 150:
        return "greedy_capture"

    # 3. Missed back-rank mate
    if _has_back_rank_mate(board_after):
        return "back_rank_blindness"

    # 4. Hope chess - large blunder with no immediate forcing reply
    if cpl > 300:
        return "hope_chess"

    # 5. One-mover blindness - missed a checkmate in one
    if _missed_one_mover(board_before, move, best_move):
        return "one_mover_blindness"

    return "general_inaccuracy"

def process_game(game, stockfish_path, depth):
    """Annotate a single game and return a list of result dicts."""
    results = []
    engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
    board = game.board()
    white_elo = game.headers.get("WhiteElo")
    black_elo = game.headers.get("BlackElo")
    
    for move in game.mainline_moves():
        board_before = board.copy()
        # Engine evaluation BEFORE the move
        info_before = engine.analyse(board_before, chess.engine.Limit(depth=depth))
        score_before = info_before["score"].white().score(mate_score=10000)
        best_move = None
        if "pv" in info_before and info_before["pv"]:
            best_move = info_before["pv"][0]
            
        board.push(move)
        # Engine evaluation AFTER the move
        info_after = engine.analyse(board, chess.engine.Limit(depth=depth))
        score_after = info_after["score"].white().score(mate_score=10000)
        
        # Centipawn loss (CPL) from player's perspective
        if board_before.turn == chess.WHITE:
            cpl = score_before - score_after if score_before is not None and score_after is not None else 0
        else:
            cpl = score_after - score_before if score_before is not None and score_after is not None else 0
            
        failure = classify_failure(board_before, move, cpl, best_move)
        
        results.append({
            "fen": board_before.fen(),
            "move": move.uci(),
            "cpl": cpl,
            "failure_mode": failure,
            "elo": white_elo if board_before.turn == chess.WHITE else black_elo,
            "game_id": game.headers.get("Site", "")
        })
    engine.quit()
    return results

def main():
    parser = argparse.ArgumentParser(description="Humanlike Chess Engine annotation pipeline")
    parser.add_argument("--pgn", default=os.path.join(DATA_DIR, "lichess_2014_01.pgn.zst"), help="Path to compressed PGN file")
    parser.add_argument("--stockfish", default="/opt/homebrew/bin/stockfish", help="Path to Stockfish binary")
    parser.add_argument("--depth", type=int, default=15, help="Stockfish search depth")
    parser.add_argument("--max-games", type=int, default=200, help="Maximum number of games to process")
    parser.add_argument("--output", default=os.path.join(DATA_DIR, "annotated.jsonl"), help="Path for output JSONL file")
    args = parser.parse_args()

    if not os.path.exists(args.pgn):
        print(f"PGN file not found: {args.pgn}. Run download_pgn.py first.")
        return

    games = list(stream_games(args.pgn, max_games=args.max_games))
    total = len(games)
    print(f"Loaded {total} games from PGN. Processing with Stockfish at depth {args.depth}...")

    cpu_count = max(1, multiprocessing.cpu_count() - 1)
    print(f"Spawning {cpu_count} workers in parallel...")
    
    with ProcessPoolExecutor(max_workers=cpu_count) as executor, open(args.output, "w", encoding="utf-8") as out_f:
        future_to_idx = {executor.submit(process_game, g, args.stockfish, args.depth): idx for idx, g in enumerate(games)}
        for future in tqdm(as_completed(future_to_idx), total=total, desc="Progress"):
            try:
                results = future.result()
                for row in results:
                    out_f.write(json.dumps(row) + "\n")
            except Exception as e:
                print(f"Error processing game: {e}")

if __name__ == "__main__":
    main()
