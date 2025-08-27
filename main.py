from email.utils import formataddr
import random
import string
from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import Field
import uuid
from dotenv import load_dotenv
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from beanie import init_beanie, Document
import motor.motor_asyncio
from datetime import datetime
from typing import List

app = FastAPI()
load_dotenv("SECRET_KEY.env")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("API_FRONTEND_URL"),"http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#MongoDB setup
MONGO_URL = os.getenv("DATABASE_URL")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
db = client.sudokuDB  # sudokuDB là tên database trong URI

@app.on_event("startup")
async def app_init():
    await init_beanie(
        database=db,
        document_models=[User, GameState, VerificationCode]
    )


# JWT setup
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# SMTP setup
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Database models
class User(Document):
    id: str
    username: str
    email: EmailStr
    hashed_password: str

    class Settings:
        name = "users"  # collection name
        indexes =  ["email", "username"]


class GameState(Document):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    user_id: str
    board: List[List[int]]
    initial_puzzle: List[List[int]]
    solution: List[List[int]]
    time_played: int
    level: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_hidden: bool = False

    class Settings:
        name = "game_states"
        indexes = ["user_id"]


class VerificationCode(Document):
    user_id: str
    code: str
    purpose: str  # "registration" hoặc "password_reset"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime

    class Settings:
        name = "verification_codes"


# Pydantic models
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    username: str
    email: EmailStr
    
    class Config:
        orm_mode = True

class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: str
    username: str
    email: EmailStr

class GameStateCreate(BaseModel):
    user_id: str
    board: List[List[int]]
    initial_puzzle: List[List[int]]
    solution: List[List[int]]
    time_played: int
    level: str
    is_hidden: bool = True

class GameStateUpdate(BaseModel):
    board: List[List[int]]
    time_played: int
    is_hidden: bool = False

class GameStateResponse(BaseModel):
    id: str
    user_id: str
    board: List[List[int]]
    initial_puzzle: List[List[int]]
    solution: List[List[int]]
    time_played: int
    level: str
    created_at: datetime
    is_hidden: bool

    class Config:
        orm_mode = True

class VerificationRequest(BaseModel):
    email: EmailStr

class VerificationCodeSubmit(BaseModel):
    email: EmailStr
    code: str

class PasswordReset(BaseModel):
    email: EmailStr
    code: str
    new_password: str


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

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Không thể xác thực thông tin đăng nhập",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = await User.find_one(User.email == email)
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

# Create a random verification code
def generate_verification_code(length=6):
    return ''.join(random.choices(string.digits, k=length))

# Send verification email in SMTP
def send_verification_email(to_email: str, code: str, purpose: str):
    # Load environment variables
    from_email = SMTP_EMAIL
    password = SMTP_PASSWORD
    
    subject = "Mã xác minh tài khoản đăng ký" if purpose == "registration" else "Mã xác minh đặt lại mật khẩu"
    process = "đăng ký" if purpose == "registration" else "đặt lại mật khẩu"
    body = f"""
    <html>
        <body>
            <h2>Xin chào bạn,<h2>
            <p>Chúng tôi đã nhận được yêu cầu {process} tài khoản của bạn.</p>
            
            <p>Mã xác minh {process} của bạn là: <strong>{code}</strong></p>
            
            <p>Vui lòng nhập mã này để hoàn tất quá trình {process} tài khoản của bạn. Mã này sẽ hết hạn sau 10 phút.</p>
            
            <p>Nếu bạn không yêu cầu mã xác minh này, vui lòng bỏ qua email này.</p>
            
            <p>Trân trọng,<br>Đội ngũ hỗ trợ</p>
        </body>
    </html>
    """
    # Create the email message
    msg = MIMEMultipart()
    msg['From'] = formataddr(("Sudoku Support", from_email))
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    
    # Try to send the email
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(from_email, password)
            server.sendmail(from_email, to_email, msg.as_string())
            print("Email sent successfully")
    except Exception as e:
        print(f"Failed to send email: {e}")
        raise HTTPException(status_code=500, detail="Không thể gửi email xác minh. Vui lòng thử lại sau.")

# Endpoints
@app.post("/register", response_model=Token)
async def register(user: UserCreate, background_tasks: BackgroundTasks):
    # kiểm tra email và username
    db_user = await User.find_one(User.email == user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="Email đã được đăng ký")

    db_username = await User.find_one(User.username == user.username)
    if db_username:
        raise HTTPException(status_code=400, detail="Tên người dùng đã tồn tại")

    # tạo user
    hashed_password = get_password_hash(user.password)
    user_id = str(uuid.uuid4())

    new_user = User(
        id=user_id,
        username=user.username,
        email=user.email.lower(),
        hashed_password=hashed_password
    )
    await new_user.insert()

    # tạo verification code
    code = generate_verification_code()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    verification_code = VerificationCode(
        user_id=user_id,
        code=code,
        purpose="registration",
        expires_at=expires_at
    )
    await verification_code.insert()

    send_verification_email(user.email, code, "registration")
    
    # gửi email trong background thay vì chặn request
    background_tasks.add_task(send_verification_email, user.email, code, "registration")

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email, "username": user.username}, 
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": new_user.id,
        "username": new_user.username,
        "email": new_user.email
    }

    
@app.post("/verify-registration")
async def verify_registration(verification: VerificationCodeSubmit):
    # Tìm verification code
    verification_code = await VerificationCode.find_one(
        VerificationCode.code == verification.code,
        VerificationCode.purpose == "registration"
    )
    if not verification_code:
        raise HTTPException(status_code=400, detail="Mã xác minh không hợp lệ")

    if verification_code.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Mã xác minh đã hết hạn")

    # Kiểm tra user khớp với code và email
    user = await User.find_one(
        User.id == verification_code.user_id, User.email == verification.email
    )
    if not user:
        raise HTTPException(status_code=400, detail="Không tìm thấy người dùng")

    # Xoá verification code sau khi dùng
    await verification_code.delete()

    return {"message": "Xác minh thành công"}


@app.post("/verify-code")
async def verify_code(verification: VerificationCodeSubmit):
    verification_code = await VerificationCode.find_one(
        VerificationCode.code == verification.code,
        VerificationCode.purpose == "password_reset"
    )
    if not verification_code:
        raise HTTPException(status_code=400, detail="Mã xác minh không hợp lệ")
    if verification_code.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Mã xác minh đã hết hạn")
    user = await User.find_one(
        User.id == verification_code.user_id, User.email == verification.email
    )
    if not user:
        raise HTTPException(status_code=400, detail="Không tìm thấy người dùng")
    await verification_code.delete()
    return {"message": "Mã xác minh hợp lệ"}

@app.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await User.find_one(User.email == form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email không tồn tại hoặc mật khẩu không hợp lệ",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email, "username": user.username}, expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "email": user.email
    }

@app.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "username": current_user.username, "email": current_user.email}


@app.post("/forgot-password")
async def forgot_password(request: VerificationRequest):
    # Tìm user theo email
    user = await User.find_one(User.email == request.email)
    if not user:
        raise HTTPException(status_code=404, detail="Email không tồn tại")
    
    # Tạo mã xác minh
    code = generate_verification_code()
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    verification_code = VerificationCode(
        user_id=user.id,
        code=code,
        purpose="password_reset",
        expires_at=expires_at
    )
    await verification_code.insert()  # lưu vào MongoDB
    
    # Gửi email thực tế
    send_verification_email(request.email, code, "forgot_password")
    
    return {"message": "Mã xác minh đã được gửi đến email của bạn"}


@app.post("/reset-password")
async def reset_password(reset: PasswordReset):
    verification_code = await VerificationCode.find_one(
        VerificationCode.code == reset.code,
        VerificationCode.purpose == "password_reset"
    )
    if not verification_code:
        raise HTTPException(status_code=400, detail="Mã xác minh không hợp lệ")
    if verification_code.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Mã xác minh đã hết hạn")
    user = await User.find_one(
        User.id == verification_code.user_id, User.email == reset.email
    )
    if not user:
        raise HTTPException(status_code=400, detail="Không tìm thấy người dùng")
    
    # Cập nhật mật khẩu
    user.hashed_password = get_password_hash(reset.new_password)
    await user.save()
    await verification_code.delete()
    
    return {"message": "Mật khẩu đã được đặt lại thành công"}

@app.post("/game", response_model=GameStateResponse)
async def create_game(game: GameStateCreate, current_user: User = Depends(get_current_user)):
    # Kiểm tra quyền
    if game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền tạo trò chơi cho người dùng khác")

    # Tạo game mới
    new_game = GameState(
        user_id=game.user_id,
        board=game.board,
        initial_puzzle=game.initial_puzzle,
        solution=game.solution,
        time_played=game.time_played,
        level=game.level,
        is_hidden=game.is_hidden
    )
    await new_game.insert()

    return new_game

@app.get("/game/{user_id}", response_model=list[GameStateResponse])
async def get_games(user_id: str, current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền xem trò chơi của người dùng khác")
    games = await GameState.find(
        GameState.user_id == user_id, GameState.is_hidden == False
    ).to_list()
    return games


@app.get("/hint/{game_id}")
async def get_hint(game_id: str, current_user: User = Depends(get_current_user)):
    db_game = await GameState.get(game_id)
    if not db_game:
        raise HTTPException(status_code=404, detail="Không tìm thấy ván chơi")
    if db_game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền truy cập trò chơi này")
    board = db_game.board
    solution = db_game.solution
    
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
async def update_game(game_id: str, game: GameStateUpdate, current_user: User = Depends(get_current_user)):
    db_game = await GameState.get(game_id)
    if not db_game:
        raise HTTPException(status_code=404, detail="Không tìm thấy ván chơi")
    if db_game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền cập nhật trò chơi này")
    db_game.board = game.board
    db_game.time_played = game.time_played
    db_game.is_hidden = game.is_hidden
    await db_game.save()
    return db_game

@app.delete("/game/{game_id}")
async def delete_game(game_id: str, current_user: User = Depends(get_current_user)):
    db_game = await GameState.get(game_id)
    if not db_game:
        raise HTTPException(status_code=404, detail="Không tìm thấy ván chơi")
    if db_game.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền xóa trò chơi này")
    await db_game.delete()
    return {"message": "Game deleted"}