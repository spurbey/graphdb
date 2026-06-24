from auth.models import create_user, get_user


def signup(username, password):
    if get_user(username):
        return "User already exists"
    create_user(username, password)
    return "Signup successful"


def login(username, password):
    user = get_user(username)
    if user == password:
        return "Login successful"
    return "Invalid credentials"
