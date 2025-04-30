from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import ForeignKey, create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import json
import uuid
from dotenv import load_dotenv
import os

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SQLite database setup
DATABASE_URL = "sqlite:///./sudoku.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# JWT setup
load_dotenv("SECRET_KEY.env")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Database model
class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    games = relationship("GameState", back_populates="user")

class GameState(Base):
    __tablename__ = "game_states"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    board = Column(Text)
    initial_puzzle = Column(Text)
    solution = Column(Text)
    time_played = Column(Integer)
    level = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_hidden = Column(Boolean, default=False)
    user = relationship("User", back_populates="games")

Base.metadata.create_all(bind=engine)

# Pydantic models
class UserCreate(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    user_id: str
    username: str

class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    username: str

class GameStateCreate(BaseModel):
    user_id: str
    board: list
    initial_puzzle: list
    solution: list
    time_played: int
    level: str
    is_hidden: bool = True

class GameStateUpdate(BaseModel):
    board: list
    time_played: int
    is_hidden: bool = False

class GameStateResponse(BaseModel):
    id: int
    user_id: str
    board: list
    initial_puzzle: list
    solution: list
    time_played: int
    level: str
    created_at: datetime
    is_hidden: bool

    class Config:
        orm_mode = True

# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Authentication functions
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Không thể xác thực thông tin đăng nhập",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

# Hàm tìm ứng cử viên hợp lệ
def get_candidates(board, row, col):
    candidates = set(range(1, 10))
    
    # Kiểm tra hàng
    candidates -= set(board[row][j] for j in range(9) if board[row][j] != 0)
    
    # Kiểm tra cột
    candidates -= set(board[i][col] for i in range(9) if board[i][col] != 0)
    
    # Kiểm tra ô 3x3
    start_row, start_col = 3 * (row // 3), 3 * (col // 3)
    for i in range(start_row, start_row + 3):
        for j in range(start_col, start_col + 3):
            if board[i][j] != 0:
                candidates -= {board[i][j]}
    
    return list(candidates)

# Hàm kiểm tra ô có giá trị sai dựa trên lời giải đúng
def is_incorrect_cell(board, solution, row, col):
    if board[row][col] == 0:
        return False
    return board[row][col] != solution[row][col]

# Endpoints
@app.post("/register", response_model=Token)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Tên đăng nhập đã tồn tại")
    hashed_password = get_password_hash(user.password)
    user_id = str(uuid.uuid4())
    db_user = User(id=user_id, username=user.username, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": db_user.id,
        "username": db_user.username
    }

@app.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tên đăng nhập không tồn tại",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mật khẩu không đúng",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username
    }

@app.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return {"user_id": current_user.id, "username": current_user.username}

@app.post("/game", response_model=GameStateResponse)
async def create_game(game: GameStateCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền tạo trò chơi cho người dùng khác")
    db_game = GameState(
        user_id=game.user_id,
        board=json.dumps(game.board),
        initial_puzzle=json.dumps(game.initial_puzzle),
        solution=json.dumps(game.solution),
        time_played=game.time_played,
        level=game.level,
        is_hidden=game.is_hidden
    )
    db.add(db_game)
    db.commit()
    db.refresh(db_game)
    db_game.board = json.loads(db_game.board)
    db_game.initial_puzzle = json.loads(db_game.initial_puzzle)
    db_game.solution = json.loads(db_game.solution)
    return db_game

@app.get("/game/{user_id}", response_model=list[GameStateResponse])
async def get_games(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền xem trò chơi của người dùng khác")
    games = db.query(GameState).filter(GameState.user_id == user_id, GameState.is_hidden == False).all()
    for game in games:
        game.board = json.loads(game.board)
        game.initial_puzzle = json.loads(game.initial_puzzle)
        game.solution = json.loads(game.solution)
    return games

@app.get("/hint/{game_id}")
async def get_hint(game_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_game = db.query(GameState).filter(GameState.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Không tìm thấy ván chơi")
    if db_game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền truy cập trò chơi này")
    board = json.loads(db_game.boardimePlayed)
    solution = json.loads(db_game.solution)
    
    # Tìm ô sai hoặc ô trống
    hint_cell = None
    hint_value = None
    is_incorrect = False
    explanation = ""
    
    # Ưu tiên ô sai dựa trên lời giải đúng
    for i in range(9):
        for j in range(9):
            if is_incorrect_cell(board, solution, i, j):
                hint_cell = {"row": i, "col": j}
                hint_value = solution[i][j]
                is_incorrect = True
                break
        if hint_cell:
            break
    
    # Nếu không có ô sai, tìm ô trống
    if not hint_cell:
        min_candidates = 10
        for i in range(9):
            for j in range(9):
                if board[i][j] == 0:
                    candidates = get_candidates(board, i, j)
                    if len(candidates) < min_candidates and len(candidates) > 0:
                        min_candidates = len(candidates)
                        hint_cell = {"row": i, "col": j}
                        hint_value = solution[i][j]
    
    if not hint_cell:
        raise HTTPException(status_code=400, detail="Không có gợi ý nào khả dụng")
    
    # Tạo lời giải thích
    row, col = hint_cell["row"], hint_cell["col"]
    if is_incorrect:
        explanation = f"Ô ở hàng {row + 1}, cột {col + 1} chứa số {board[row][col]} là sai so với lời giải đúng. Số đúng phải là {hint_value} vì: "
    else:
        explanation = f"Ô ở hàng {row + 1}, cột {col + 1} có thể điền số {hint_value} vì: "
    
    # Kiểm tra hàng
    row_nums = [num for num in board[row] if num != 0 and num != board[row][col]]
    explanation += f"Hàng {row + 1} chứa các số {row_nums or 'không có số nào'}. Số {hint_value} không có trong hàng này. "
    
    # Kiểm tra cột
    col_nums = [board[i][col] for i in range(9) if board[i][col] != 0 and (i != row or not is_incorrect)]
    explanation += f"Cột {col + 1} chứa các số {col_nums or 'không có số nào'}. Số {hint_value} không có trong cột này. "
    
    # Kiểm tra ô 3x3
    start_row, start_col = 3 * (row // 3), 3 * (col // 3)
    box_nums = [board[i][j] for i in range(start_row, start_row + 3) for j in range(start_col, start_col + 3) if board[i][j] != 0 and (i != row or j != col or not is_incorrect)]
    explanation += f"Ô 3x3 chứa ô này có các số {box_nums or 'không có số nào'}. Số {hint_value} không có trong ô 3x3 này."
    
    return {
        "row": row,
        "col": col,
        "value": hint_value,
        "explanation": explanation,
        "is_incorrect": is_incorrect
    }

@app.put("/game/{game_id}", response_model=GameStateResponse)
async def update_game(game_id: int, game: GameStateUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_game = db.query(GameState).filter(GameState.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Không tìm thấy ván chơi")
    if db_game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền cập nhật trò chơi này")
    db_game.board = json.dumps(game.board)
    db_game.time_played = game.time_played
    db_game.is_hidden = game.is_hidden
    db.commit()
    db.refresh(db_game)
    db_game.board = json.loads(db_game.board)
    db_game.initial_puzzle = json.loads(db_game.initial_puzzle)
    db_game.solution = json.loads(db_game.solution)
    return db_game

@app.delete("/game/{game_id}")
async def delete_game(game_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_game = db.query(GameState).filter(GameState.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Không tìm thấy ván chơi")
    if db_game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền xóa trò chơi này")
    db.delete(db_game)
    db.commit()
    return {"message": "Game deleted"}