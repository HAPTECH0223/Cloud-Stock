from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import threading
import time
import queue
import os

app = Flask(__name__)
CORS(app)

class CloudStockfishEngine:
    def __init__(self):
        self.process = None
        self.output_queue = queue.Queue()
        self.stockfish_path = self.find_stockfish()
        self.start_engine()
        
    def find_stockfish(self):
        """Find Stockfish binary - prioritize local copy"""
        # Check for local binary first
        local_paths = ["./stockfish", "stockfish"]
        
        for path in local_paths:
            if os.path.exists(path):
                try:
                    # Make sure it's executable
                    os.chmod(path, 0o755)
                    print(f"✅ Found local Stockfish: {path}")
                    return path
                except:
                    continue
        
        # Fallback to system installation
        system_paths = ["/usr/bin/stockfish", "/usr/local/bin/stockfish"]
        for path in system_paths:
            if os.path.exists(path):
                print(f"✅ Found system Stockfish: {path}")
                return path
        
        raise Exception("❌ Stockfish binary not found! Make sure to include 'stockfish' file in your repository.")
        
    def start_engine(self):
        try:
            print(f"🚀 Starting Stockfish: {self.stockfish_path}")
            
            self.process = subprocess.Popen(
                [self.stockfish_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )
            
            # Start output reader thread
            self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self.reader_thread.start()
            
            # Initialize engine
            self.send_command("uci")
            self.wait_for("uciok", timeout=10)
            self.send_command("isready")
            self.wait_for("readyok", timeout=10)
            
            print("✅ Stockfish engine ready!")
            
        except Exception as e:
            print(f"❌ Failed to start Stockfish: {e}")
            raise
    
    def _read_output(self):
        """Read output from Stockfish"""
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if line:
                    line = line.strip()
                    self.output_queue.put(line)
                    # Log important responses
                    if any(keyword in line for keyword in ["bestmove", "uciok", "readyok"]):
                        print(f"🎯 Stockfish: {line}")
                else:
                    break
            except Exception as e:
                print(f"Error reading output: {e}")
                break
    
    def send_command(self, command):
        """Send command to Stockfish"""
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(command + '\n')
                self.process.stdin.flush()
            except Exception as e:
                print(f"Error sending command: {e}")
    
    def wait_for(self, expected_response, timeout=15):
        """Wait for specific response"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = self.output_queue.get(timeout=0.5)
                if expected_response in response:
                    return response
            except queue.Empty:
                continue
        
        print(f"⏰ Timeout waiting for: {expected_response}")
        return None
    
    def get_best_move(self, fen, depth=25, time_limit=None):
        """Analyze position and get best move"""
        try:
            print(f"🔍 Analyzing depth {depth}: {fen[:50]}...")
            
            # Check if engine is alive
            if not self.process or self.process.poll() is not None:
                print("💀 Engine died, restarting...")
                self.start_engine()
            
            # Clear output queue
            while not self.output_queue.empty():
                try:
                    self.output_queue.get_nowait()
                except queue.Empty:
                    break
            
            # Set position
            self.send_command(f"position fen {fen}")
            
            # Start search
            if time_limit:
                search_cmd = f"go movetime {min(time_limit * 1000, 25000)}"
                timeout = time_limit + 8
            else:
                depth = min(max(depth, 5), 30)  # Clamp 5-30
                search_cmd = f"go depth {depth}"
                timeout = max(depth * 2, 30)
            
            print(f"🚀 Search: {search_cmd}")
            self.send_command(search_cmd)
            
            # Wait for result
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                try:
                    response = self.output_queue.get(timeout=1.0)
                    
                    if response.startswith("bestmove"):
                        parts = response.split()
                        if len(parts) >= 2 and parts[1] != "(none)":
                            move = parts[1]
                            elapsed = time.time() - start_time
                            print(f"✅ Best move: {move} ({elapsed:.1f}s)")
                            return move
                        else:
                            print("❌ No legal moves")
                            return None
                            
                except queue.Empty:
                    if self.process.poll() is not None:
                        print("💀 Process died during search")
                        return None
                    continue
            
            print(f"⏰ Search timeout ({timeout}s)")
            return None
            
        except Exception as e:
            print(f"❌ Analysis error: {e}")
            return None

# Global engine
engine = None

@app.route('/')
def home():
    return jsonify({
        'message': '🚀 Cloud Stockfish Server Online!',
        'status': 'running',
        'endpoints': {
            'analyze': '/get_best_move?fen=<fen>&depth=<depth>',
            'health': '/health'
        },
        'example': '/get_best_move?fen=rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR%20w%20KQkq%20-%200%201&depth=25'
    })

@app.route('/get_best_move', methods=['GET'])
def get_best_move():
    global engine
    
    try:
        fen = request.args.get('fen')
        depth = request.args.get('depth', type=int, default=25)
        time_limit = request.args.get('time_limit', type=int)
        
        if not fen:
            return jsonify({
                'success': False,
                'error': 'Missing FEN parameter'
            }), 400
        
        if not engine:
            return jsonify({
                'success': False,
                'error': 'Engine not ready'
            }), 500
        
        # Validate depth
        depth = max(5, min(depth, 30))
        
        # Analyze
        start_time = time.time()
        best_move = engine.get_best_move(fen, depth=depth, time_limit=time_limit)
        analysis_time = time.time() - start_time
        
        if not best_move:
            return jsonify({
                'success': False,
                'error': 'Analysis failed or timeout',
                'analysis_time': round(analysis_time, 2)
            })
        
        return jsonify({
            'success': True,
            'best_move': best_move,
            'fen': fen,
            'depth': depth,
            'analysis_time': round(analysis_time, 2),
            'engine': 'Stockfish 17',
            'server': 'Railway Cloud'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/health', methods=['GET'])
def health():
    global engine
    
    if not engine:
        return jsonify({
            'status': 'unhealthy',
            'message': '❌ Engine not initialized'
        })
    
    # Quick test
    try:
        start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        test_move = engine.get_best_move(start_fen, depth=5, time_limit=3)
        
        if test_move:
            return jsonify({
                'status': 'healthy',
                'test_move': test_move,
                'message': '✅ Stockfish working perfectly!',
                'stockfish_path': engine.stockfish_path
            })
        else:
            return jsonify({
                'status': 'degraded',
                'message': '⚠️ Engine responding slowly'
            })
            
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'message': f'❌ Engine test failed: {str(e)}'
        })

if __name__ == '__main__':
    try:
        print("🌟 Initializing Cloud Stockfish Server...")
        engine = CloudStockfishEngine()
        
        port = int(os.environ.get("PORT", 5000))
        print(f"🌐 Server starting on port {port}")
        
        app.run(host='0.0.0.0', port=port, debug=False)
        
    except Exception as e:
        print(f"💥 Failed to start: {e}")
        exit(1)