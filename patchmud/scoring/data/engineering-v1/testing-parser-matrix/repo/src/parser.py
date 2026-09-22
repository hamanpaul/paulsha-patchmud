def parse(text):
    lines = [line for line in text.splitlines() if line]
    headers = lines[0].split(",")
    rows = []
    for line in lines[1:]:
        values = line.split(",")
        if len(values) != len(headers):
            raise ValueError("row width")
        rows.append(dict(zip(headers, values)))
    return rows
