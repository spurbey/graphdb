users = {}


def create_user(username, password):
    """Create a new user with username and password."""
    if not username or not password:
        raise ValueError("Username and password cannot be empty")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    users[username] = password
    return True


def get_user(username):
    """Retrieve user password by username."""
    return users.get(username)


def delete_user(username):
    """Delete a user by username."""
    if username in users:
        del users[username]
        return True
    return False


def list_users():
    """List all registered usernames."""
    return list(users.keys())


def user_exists(username):
    """Check if user exists."""
    return username in users
