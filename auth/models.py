users = {}


def create_user(username, password):
    users[username] = password


def get_user(username):
    return users.get(username)
