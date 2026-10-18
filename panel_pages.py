"""Safe server-rendered pages for local accounts and address books."""
from __future__ import annotations

from html import escape as _escape
from urllib.parse import quote
import accounts


def esc(value):
    return _escape(str(value if value is not None else ""), quote=True)


def options_for_role(current):
    return "".join(f'<option value="{value}"{" selected" if value == current else ""}>{text}</option>'
                   for value, text in (("admin","Администратор"),("editor","Редактор"),("viewer","Читатель")))


def users_page(db, user, csrf):
    if user["role"] != "admin":
        raise accounts.AccessDenied("Только администратор управляет пользователями")
    content = [
        '<header><div><h1>Учётные записи</h1><p class="muted">Доступ к панели и адресным книгам, не к самим подключениям RustDesk.</p></div><a href="/">Устройства</a> · <a href="/books">Адресные книги</a></header>',
        '<section><h2>Добавить пользователя</h2>',
        '<form method="post" action="/user/create">',
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">',
        '<label>Логин (латиница, цифры, точка, дефис, подчёркивание)<input name="username" maxlength="64" required></label>',
        '<label>Пароль, минимум 14 символов<input name="password" type="password" autocomplete="new-password" minlength="14" required></label>',
        f'<label>Роль<select name="role">{options_for_role("viewer")}</select></label>',
        '<button>Создать</button></form></section>',
        '<section><h2>Пользователи</h2><p class="muted">Администратор создаёт книги и управляет пользователями. Редактор ведёт контакты. Читатель видит доступные книги, но не редактирует.</p></section>',
    ]
    for row in accounts.user_listing(db):
        content.append(f'''<article><h2>{esc(row["username"])} <small>#{int(row["id"])}</small></h2>
<form method="post" action="/user/update">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<input type="hidden" name="id" value="{int(row["id"])}">
<label>Роль<select name="role">{options_for_role(row["role"])}</select></label>
<label>Состояние<select name="active">
<option value="1"{" selected" if row["active"] else ""}>Активен</option>
<option value="0"{" selected" if not row["active"] else ""}>Отключён</option>
</select></label>
<label>Новый пароль (оставь пустым, чтобы не менять)<input name="password" type="password" autocomplete="new-password" minlength="14"></label>
<button>Сохранить</button></form></article>''')
    return "".join(content)


def books_page(db, user, csrf, book_choice, peers):
    books = accounts.books_visible(db, user)
    if book_choice:
        try:
            book_id = int(book_choice)
        except (TypeError, ValueError):
            raise accounts.BadInput("Некорректная книга")
    else:
        book_id = books[0]["id"] if books else None
    if book_id is not None:
        selected = accounts.book_access(db, user, book_id)
        entries = accounts.contacts_in_book(db, book_id)
    else:
        selected, entries = None, []
    editable = user["role"] in ("admin", "editor")
    header = ['<header><div><h1>Адресные книги</h1><p class="muted">Контакты доступны участникам панели по их роли.</p></div>'
              '<a href="/">Устройства</a>'
              + (' · <a href="/users">Пользователи</a>' if user["role"] == "admin" else "") + '</header>']
    header.append('<section><h2>Книги</h2><p> ')
    for book in books:
        private = book["owner_user_id"] is not None
        title = esc(book["name"]) + (" (личная)" if private else " (общая)")
        is_selected = book["id"] == book_id
        header.append(f'<a href="/books?book={book["id"]}">{"<strong>" if is_selected else ""}{title}{"</strong>" if is_selected else ""}</a> &nbsp; ')
    header.append('</p></section>')
    if editable:
        types = '<option value="personal">Личная</option>'
        if user["role"] == "admin":
            types += '<option value="shared">Общая (доступна всем пользователям панели)</option>'
        header.append(f'''<section><h2>Создать адресную книгу</h2>
<form action="/book/create" method="post">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<label>Название<input name="name" maxlength="80" required></label>
<label>Тип<select name="scope">{types}</select></label>
<button>Создать книгу</button></form></section>''')
    if selected:
        header.append(f'<section><h2>{esc(selected["name"])}</h2><p class="muted">Контактов: {len(entries)}. '
                      'Данные хранятся в базе панели и не передаются автоматически стандартным клиентам RustDesk OSS.</p>'
                      f'<a href="/book/export.csv?book={book_id}">Скачать CSV</a> &nbsp; '
                      f'<a href="/book/export.json?book={book_id}">Скачать JSON</a></section>')
        if editable:
            header.append(f'''<section><h2>Добавить или изменить контакт</h2>
<form method="post" action="/book/contact">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<input type="hidden" name="book" value="{book_id}">
<label>RustDesk ID<input name="peer_id" maxlength="128" required></label>
<label>Название<input name="alias" maxlength="120"></label>
<label>Метки через запятую<input name="tags" maxlength="200"></label>
<label>Заметка<textarea name="note" maxlength="1200"></textarea></label>
<button>Сохранить контакт</button></form></section>
<section><form method="post" action="/book/import-hbbs">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<input type="hidden" name="book" value="{book_id}">
<button>Добавить отсутствующие устройства из HBBS</button></form>
<small>Имеющиеся записи и их названия не перезаписываются.</small></section>''')
        header.append('<div class="cards">')
        for item in entries:
            if editable:
                editor = f'''<form method="post" action="/book/contact">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<input type="hidden" name="book" value="{book_id}">
<input type="hidden" name="peer_id" value="{esc(item["peer_id"])}">
<label>Название<input name="alias" maxlength="120" value="{esc(item["alias"])}"></label>
<label>Метки<input name="tags" maxlength="200" value="{esc(item["tags"])}"></label>
<label>Заметка<textarea name="note" maxlength="1200">{esc(item["note"])}</textarea></label>
<button>Сохранить</button></form>
<form method="post" action="/book/remove">
<input type="hidden" name="csrf" value="{esc(csrf)}">
<input type="hidden" name="book" value="{book_id}">
<input type="hidden" name="peer_id" value="{esc(item["peer_id"])}">
<button>Удалить из книги</button></form>'''
            else:
                editor = f'<p class="meta">Метки: {esc(item["tags"])} · {esc(item["note"])}</p>'
            header.append(f'<article><strong>{esc(item["alias"] or item["peer_id"])}</strong>'
                          f'<div class="meta">ID: {esc(item["peer_id"])}</div>{editor}</article>')
        header.append('</div>')
    return "".join(header)
