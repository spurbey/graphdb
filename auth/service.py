from auth.models import create_user, get_user, delete_user, user_exists


class AuthError(Exception):
    """Base exception for authentication errors."""
    pass


class ValidationError(AuthError):
    """Raised when input validation fails."""
    pass


class AuthenticationError(AuthError):
    """Raised when authentication fails."""
    pass


def validate_username(username):
    """Validate username format."""
    if not username:
        raise ValidationError("Username cannot be empty")
    if len(username) < 3:
        raise ValidationError("Username must be at least 3 characters")
    if not username.isalnum():
        raise ValidationError("Username must be alphanumeric")
    return True


def signup(username, password):
    """Register a new user with validation and password strength check."""
    try:
        validate_username(username)
        if len(password) < 8:
            raise ValidationError("Password must be at least 8 characters")
        if user_exists(username):
            raise ValidationError("User already exists")
        create_user(username, password)
        return {"success": True, "message": "Signup successful", "username": username}
    except (ValidationError, ValueError) as e:
        return {"success": False, "error": str(e)}


def login(username, password):
    """Authenticate user and return success status."""
    try:
        if not username or not password:
            raise AuthenticationError("Username and password required")
        
        stored_password = get_user(username)
        
        if stored_password is None:
            raise AuthenticationError("User not found")
        
        if stored_password != password:
            raise AuthenticationError("Invalid password")
        
        return {"success": True, "message": "Login successful", "username": username}
    
    except AuthenticationError as e:
        return {"success": False, "error": str(e)}


def logout(username):
    """Logout user (placeholder for session management)."""
    if user_exists(username):
        return {"success": True, "message": f"User {username} logged out"}
    return {"success": False, "error": "User not found"}


def delete_account(username, password):
    """Delete user account with password verification."""
    try:
        login_result = login(username, password)
        if not login_result["success"]:
            raise AuthenticationError("Authentication failed")
        
        if delete_user(username):
            return {"success": True, "message": "Account deleted successfully"}
        
        return {"success": False, "error": "Failed to delete account"}
    
    except AuthenticationError as e:
        return {"success": False, "error": str(e)}
