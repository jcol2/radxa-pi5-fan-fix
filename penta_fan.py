#!/usr/bin/env python3***
import time
import lgpio
import subprocess
import json

# -------------------------------------------------------
# CONFIG
# -------------------------------------------------------

# These are confirmed working for your Radxa Penta SATA HAT fan on Pi 5
CHIP = 0               # gpiochip0
LINE = 27              # fan PWM line (from gpioinfo: FAN_PWM)

PWM_FREQ = 100         # 10kHz (highest supported on this line)
CHECK_INTERVAL = 3.0   # seconds between temperature checks
HDD_CHECK_INTERVAL = 60.0


# -------------------------------------------------------
# HELPERS
# -------------------------------------------------------

def get_cpu_temp():
    """Read CPU temperature in °C from the kernel."""
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return int(f.read().strip()) / 1000.0

def get_drive_temp(device):
    result = subprocess.run(
        ["smartctl", "-A", "-j", device],
        capture_output=True,
        text=True
    )

    data = json.loads(result.stdout)

    if "temperature" in data:
        return data["temperature"]["current"]

    return None

def cpu_temp_to_duty(t):
    """
    Map temperature (°C) to fan duty (%).

    This curve starts the fan early so it's easy to SEE/HEAR that it's working.
    You can tune the thresholds and duty steps later if you want it quieter.
    """
    if t < 56.0:
        return 70        # cold: off
    elif t < 58.0:
        return 70       # gentle
    elif t < 60.0:
        return 75
    elif t < 63.0:
        return 75
    else:
        return 100      # hot: full blast

def hdd_temp_to_duty(t):
    if t < 40.0:
        return 0
    elif t < 42.0:
        return 30
    elif t < 43.0:
        return 60
    elif t < 44.0:
        return 80
    elif t < 45.0:
        return 90
    else:
        return 100

def set_fan(handle, duty):
    """
    Set fan speed via hardware PWM.
    Kick with brief pulse first to ensure PWM takes control.
    """
    duty = max(0, min(100, int(duty)))
    # Brief kick to ensure PWM is active on the line
    lgpio.tx_pwm(handle, LINE, PWM_FREQ, 1)
    time.sleep(0.05)
    # Now set actual duty
    if duty == 0:
        lgpio.tx_pwm(handle, LINE, PWM_FREQ, 0)
        lgpio.gpio_write(handle, LINE, 0)
    else:
        lgpio.tx_pwm(handle, LINE, PWM_FREQ, duty)

# -------------------------------------------------------
# MAIN LOOP
# -------------------------------------------------------

def main():
    h = lgpio.gpiochip_open(CHIP)
    lgpio.gpio_claim_output(h, LINE)
    
    # Initialize PWM control by briefly asserting it
    lgpio.tx_pwm(h, LINE, PWM_FREQ, 100)
    time.sleep(0.5)


    last_duty = None

    FAN_ON_TEMP  = 56.0
    FAN_OFF_TEMP = 54.0
    fan_enabled = False

    last_hdd_check_sec = 0
    last_sda_temp = 0
    last_sdb_temp = 0

    try:
        while True:
            now_sec = time.monotonic()
            cpu_temp = get_cpu_temp()
            # hysteresis gate
            if fan_enabled:
                if cpu_temp <= FAN_OFF_TEMP:
                    fan_enabled = False
            else:
                if cpu_temp >= FAN_ON_TEMP:
                    fan_enabled = True
            if now_sec - last_hdd_check_sec >= HDD_CHECK_INTERVAL:
                last_hdd_check_sec = now_sec
                last_sda_temp = get_drive_temp("/dev/sda")
                last_sdb_temp = get_drive_temp("/dev/sdb")
            cpu_temp = get_cpu_temp()
            sda_temp = last_sda_temp
            sdb_temp = last_sdb_temp
            cpu_duty = cpu_temp_to_duty(cpu_temp) if fan_enabled else 0
            sda_duty = hdd_temp_to_duty(sda_temp)
            sdb_duty = hdd_temp_to_duty(sdb_temp)

            duty = max(cpu_duty, sda_duty, sdb_duty)

            # Always log temp + duty so behaviour is visible in journalctl
            print(f"penta-fan: cpu_temp={cpu_temp:.1f}°C, sda_temp={sda_temp:.1f}°C, sdb_temp={sdb_temp:.1f}°C, duty={duty}%")

            if duty != last_duty:
                set_fan(h, duty)
                last_duty = duty

            time.sleep(CHECK_INTERVAL)
    finally:
        # On exit (Ctrl+C, systemd stop), make sure the fan is turned off
        try:
            set_fan(h, 0)
        except Exception:
            lgpio.gpio_write(h, LINE, 0)
        lgpio.gpiochip_close(h)


if __name__ == "__main__":
    main()
