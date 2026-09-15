#!/usr/bin/env bash

# Safely load dotenv-style KEY=VALUE files without executing their contents.
# This intentionally supports unquoted values containing spaces so deployment
# files remain compatible with Docker Compose while avoiding `source .env`.

_dotenv_trim() {
    local dotenv_trim_value="${1-}"

    dotenv_trim_value="${dotenv_trim_value#"${dotenv_trim_value%%[![:space:]]*}"}"
    dotenv_trim_value="${dotenv_trim_value%"${dotenv_trim_value##*[![:space:]]}"}"

    printf '%s' "${dotenv_trim_value}"
}

load_dotenv_file() {
    local dotenv_file="${1:-}"
    local dotenv_raw_line=""
    local dotenv_line=""
    local dotenv_key=""
    local dotenv_value=""
    local dotenv_line_number=0
    local dotenv_first_character=""
    local dotenv_last_character=""

    if [[ -z "${dotenv_file}" ]]; then
        echo "Dotenv file path is required." >&2
        return 2
    fi

    if [[ ! -f "${dotenv_file}" ]]; then
        echo "Dotenv file does not exist: ${dotenv_file}" >&2
        return 2
    fi

    while IFS= read -r dotenv_raw_line || [[ -n "${dotenv_raw_line}" ]]; do
        dotenv_line_number=$((dotenv_line_number + 1))
        dotenv_raw_line="${dotenv_raw_line%$'\r'}"
        dotenv_line="$(_dotenv_trim "${dotenv_raw_line}")"

        if [[ -z "${dotenv_line}" || "${dotenv_line}" == \#* ]]; then
            continue
        fi

        if [[ "${dotenv_line}" =~ ^export[[:space:]]+ ]]; then
            dotenv_line="$(_dotenv_trim "${dotenv_line#export}")"
        fi

        if [[ "${dotenv_line}" != *=* ]]; then
            echo "Invalid dotenv entry at ${dotenv_file}:${dotenv_line_number}; expected KEY=VALUE." >&2
            return 2
        fi

        dotenv_key="$(_dotenv_trim "${dotenv_line%%=*}")"
        dotenv_value="$(_dotenv_trim "${dotenv_line#*=}")"

        if [[ ! "${dotenv_key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            echo "Invalid dotenv variable name at ${dotenv_file}:${dotenv_line_number}: ${dotenv_key}" >&2
            return 2
        fi

        if [[ -n "${dotenv_value}" ]]; then
            dotenv_first_character="${dotenv_value:0:1}"
            dotenv_last_character="${dotenv_value: -1}"

            if [[ "${dotenv_first_character}" == '"' || "${dotenv_first_character}" == "'" ]]; then
                if [[ "${dotenv_last_character}" != "${dotenv_first_character}" ]]; then
                    echo "Unterminated quoted dotenv value at ${dotenv_file}:${dotenv_line_number}." >&2
                    return 2
                fi

                dotenv_value="${dotenv_value:1:${#dotenv_value}-2}"
            elif [[ "${dotenv_value}" =~ ^(.*[^[:space:]])[[:space:]]+\#.*$ ]]; then
                dotenv_value="${BASH_REMATCH[1]}"
            elif [[ "${dotenv_value}" =~ ^[[:space:]]*\#.*$ ]]; then
                dotenv_value=""
            fi
        fi

        # `export name=value` is a shell builtin assignment; quoting the entire
        # argument preserves commas, spaces, dollar signs and command syntax as
        # literal data instead of executing or expanding them.
        export "${dotenv_key}=${dotenv_value}"
    done < "${dotenv_file}"
}
