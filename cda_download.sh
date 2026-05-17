
#!/bin/bash
# cda_download.sh -- download a video from cda.pl and mux it into gotowy_film.mp4
#
# The DASH manifest URL is read straight off the video page (the
# "manifest_cast" field in the player's player_data attribute).
#
# Usage:  ./cda_download.sh "https://www.cda.pl/video/XXXXXXXXX"
# My testing example: https://www.cda.pl/video/12650766f

if [ -z "$1" ]; then
  read -p "Wklej adres URL strony wideo z cda.pl: " video_url
else
  video_url="$1"
fi

# Browser headers that cda.pl expects
HDRS=(
  -H 'accept: */*'
  -H 'accept-language: en-US,en;q=0.7'
  -H 'origin: https://www.cda.pl'
  -H 'referer: https://www.cda.pl/'
  -H 'sec-ch-ua: "Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"'
  -H 'sec-ch-ua-mobile: ?0'
  -H 'sec-ch-ua-platform: "Linux"'
  -H 'sec-fetch-dest: empty'
  -H 'sec-fetch-mode: cors'
  -H 'sec-fetch-site: same-site'
  -H 'sec-gpc: 1'
  -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'
)

echo "[1/4] Pobieranie strony i odczyt manifestu DASH..."
page=$(curl -sL "${HDRS[@]}" "$video_url")

# player_data may be HTML-entity-encoded -- turn &quot; back into "
page=${page//&quot;/\"}

# Extract the "manifest_cast":"https:\/\/.../...mpd" field from player_data
manifest_cast=$(echo "$page" | grep -oP '"manifest_cast":"\K[^"]+\.mpd' | head -n1)
if [ -z "$manifest_cast" ]; then
  echo "Blad: nie znaleziono \"manifest_cast\" na stronie."
  echo "      Sprawdz adres URL (czy wideo jest publiczne?)."
  exit 1
fi

# Unescape JSON slashes  \/  ->  /
mpd_url=$(echo "$manifest_cast" | sed 's/\\\//\//g')
# Media base directory = manifest URL minus its last path segment
base_url="${mpd_url%/*}/"
echo "      Manifest: $mpd_url"

xml_response=$(curl -s "${HDRS[@]}" "$mpd_url")

# Video filename: first <BaseURL> after contentType="video"
video_file=$(echo "$xml_response" | awk '/contentType="video"/{flag=1} flag && /<BaseURL>/{gsub(/<\/?BaseURL>/,""); print $1; exit}')

# Audio filename: first <BaseURL> after contentType="audio"
audio_file=$(echo "$xml_response" | awk '/contentType="audio"/{flag=1} flag && /<BaseURL>/{gsub(/<\/?BaseURL>/,""); print $1; exit}')

# Strip any stray whitespace
video_file=$(echo "$video_file" | tr -d '[:space:]')
audio_file=$(echo "$audio_file" | tr -d '[:space:]')

if [ -z "$video_file" ] || [ -z "$audio_file" ]; then
  echo "Blad: manifest nie zawiera strumieni wideo/audio."
  exit 1
fi

echo "      Wideo plik: $video_file"
echo "      Audio plik: $audio_file"

echo "[2/4] Pobieranie pliku wideo: ${base_url}${video_file}"
curl -L -o "temp_video.mp4" "${HDRS[@]}" "${base_url}${video_file}"

echo "[3/4] Pobieranie pliku audio: ${base_url}${audio_file}"
curl -L -o "temp_audio.mp4" "${HDRS[@]}" "${base_url}${audio_file}"

echo "[4/4] Laczenie strumieni przez FFmpeg..."
ffmpeg -i "temp_video.mp4" -i "temp_audio.mp4" -c copy "gotowy_film.mp4" -y

rm -f "temp_video.mp4" "temp_audio.mp4"

echo "Gotowe -> gotowy_film.mp4"
