# https://www.cda.pl/video/12650766f

# if no first arg then read

if [ -z "$1" ]; then
  read -p "Wklej adres URL pliku wideo (z F12): " video_url
else
  video_url="$1"
fi

# get last part of url after last slash
filename="${video_url##*/}"

# sed f$ to .mpd
file_url_scheme=$(echo "$filename" | sed 's/..$//')

# echo "Audio info URL: $audio_info_url"

# curl $audio_info_url
echo "https://vwaw036.cda.pl/${file_url_scheme}raw/${file_url_scheme}.mpd"
# results in https://vwaw036.cda.pl/1265076raw/1265076.mpd

xml_response=$(curl "https://vwaw036.cda.pl/${file_url_scheme}raw/${file_url_scheme}.mpd" \
  -H 'accept: */*' \
  -H 'accept-language: en-US,en;q=0.7' \
  -H 'origin: https://www.cda.pl' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.cda.pl/' \
  -H 'sec-ch-ua: "Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Linux"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'sec-gpc: 1' \
  -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36')

# Wyciągnięcie nazwy pliku wideo (szuka linii z contentType="video" i bierze następny tag BaseURL)
video_file=$(echo "$xml_response" | awk '/contentType="video"/{flag=1} flag && /<BaseURL>/{gsub(/<\/?BaseURL>/,""); print $1; exit}')

# Wyciągnięcie nazwy pliku audio (szuka linii z contentType="audio" i bierze następny tag BaseURL)
audio_file=$(echo "$xml_response" | awk '/contentType="audio"/{flag=1} flag && /<BaseURL>/{gsub(/<\/?BaseURL>/,""); print $1; exit}')

# Usunięcie ewentualnych spacji/odstępów
video_file=$(echo "$video_file" | tr -d '[:space:]')
audio_file=$(echo "$audio_file" | tr -d '[:space:]')

echo "Wideo plik: $video_file"
echo "Audio plik: $audio_file"


video_url="https://vwaw036.cda.pl/${file_url_scheme}raw/${video_file}"
audio_url="https://vwaw036.cda.pl/${file_url_scheme}raw/${audio_file}"

echo ""
echo "[1/3] Pobieranie pliku wideo z adresu: $video_url"
curl -L -o "temp_video.mp4" "$video_url" \
  -H 'accept: */*' \
  -H 'accept-language: en-US,en;q=0.7' \
  -H 'origin: https://www.cda.pl' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.cda.pl/' \
  -H 'sec-ch-ua: "Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Linux"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'sec-gpc: 1' \
  -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'

echo "[2/3] Pobieranie pliku audio z adresu: $audio_url"
curl -L -o "temp_audio.mp4" "$audio_url" \
  -H 'accept: */*' \
  -H 'accept-language: en-US,en;q=0.7' \
  -H 'origin: https://www.cda.pl' \
  -H 'priority: u=1, i' \
  -H 'referer: https://www.cda.pl/' \
  -H 'sec-ch-ua: "Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Linux"' \
  -H 'sec-fetch-dest: empty' \
  -H 'sec-fetch-mode: cors' \
  -H 'sec-fetch-site: same-site' \
  -H 'sec-gpc: 1' \
  -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'

echo "[3/3] Łączenie strumieni przez FFmpeg..."
ffmpeg -i "temp_video.mp4" -i "temp_audio.mp4" -c copy "gotowy_film.mp4" -y

rm -f "temp_video.mp4" "temp_audio.mp4"

# https://vwaw036.cda.pl/1265076raw/1265076.mpd
